import re
from .common import digest


def cache_key(config, model, record):
    from dataclasses import asdict
    from .schema import SCHEMA
    return digest({"base_url": config.base_url, "model": asdict(model), "prompt": config.prompt, "schema": SCHEMA,
                   "text": record["text"], "context": record["context"], "language": record["language"], "version": "0.1.0"})


def quote_hint(text):
    # Apostrophes in English contractions are not quotation marks.
    return bool(re.search(r'«[^»]+»|“[^”]+”|"[^"\n]+"', text))


def aggregate(record, votes, policy, run_id, review=None, moderation=None, expected_judges=None):
    expected = expected_judges or policy.get("num_judges", 4)
    decisions = [v.get("decision") for v in votes if v["status"] == "ok"]
    n_votes = len(votes)
    valid = n_votes == expected and len(decisions) == expected and all(d["label"] is not None for d in decisions)
    positives = sum(d["label"] == 1 for d in decisions)
    negatives = sum(d["label"] == 0 for d in decisions)

    # Determine candidate label based on number of judges:
    if not valid:
        candidate = None
    elif expected == 2:
        candidate = 1 if positives == 2 else 0 if negatives == 2 else None
    elif expected == 3:
        candidate = 1 if positives >= 2 else 0 if negatives >= 2 else None
    else:  # expected >= 4
        candidate = 1 if positives >= 3 else 0 if negatives >= 3 else None

    flags, blocking = set(), set()
    errors = n_votes != expected or any(v["status"] != "ok" for v in votes)

    # Even split tie (e.g., 1-1 for 2 judges, 2-2 for 4 judges):
    is_tie = valid and (positives == negatives and positives > 0)
    tie_flag = f"tie_{positives}_{negatives}_moderation_resolved"
    mod_decision = moderation.get("decision") if (is_tie and isinstance(moderation, dict) and moderation.get("status") == "ok") else None
    if is_tie and mod_decision:
        candidate = 1 if mod_decision.get("flagged") else 0
        flags.add(tie_flag)

    if errors:
        blocking.add("incomplete_or_failed_votes")
    elif any(d["label"] is None for d in decisions):
        blocking.add("model_abstention")
    elif candidate is None:
        blocking.add(f"tie_{positives}_{negatives}")
    elif min(positives, negatives) > 0 and policy["review_minority_vote"] and not is_tie:
        flags.add("minority_vote")
        if policy["block_minority_vote"]:
            blocking.add("minority_vote")
    tags = sorted({tag for d in decisions for tag in d["discourse_tags"]})
    sensitive = set(tags).intersection(policy["sensitive_discourse_tags"])
    if quote_hint(record["text"]):
        sensitive.add("quotation")
        flags.add("quotation_surface_hint")
    for tag in sensitive:
        flags.add("review_" + tag)
        if policy["block_sensitive_discourse"]:
            blocking.add("review_" + tag)
    for v in votes:
        if v["status"] != "ok":
            continue
        d = v["decision"]
        blocking.update(v.get("warnings", []))
        if d["needs_context"]:
            blocking.add("needs_context")
        if d["label"] is not None:
            conf = d["p_offensive_raw"] if d["label"] == 1 else 1 - d["p_offensive_raw"]
            if conf < policy["min_vote_confidence_raw"]:
                blocking.add("low_vote_confidence_raw")
    # Binary disagreement is already recorded. Compare subtypes among positive votes.
    type_sets = {tuple(sorted(d["abuse_types"])) for d in decisions if d["label"] == 1}
    if policy["review_subtype_disagreement"] and len(type_sets) > 1:
        flags.add("subtype_disagreement")
        if policy.get("block_subtype_disagreement", True):
            blocking.add("subtype_disagreement")
    unanimous = valid and (positives == expected or negatives == expected)
    audit = int(digest({"run": run_id, "content": record["content_hash"]})[:8], 16) / 2**32
    if unanimous and audit < policy["audit_unanimous_rate"]:
        flags.add("unanimous_audit_sample")
    flags.update(blocking)
    final = candidate if not blocking else None
    if errors:
        status = "error"
    elif any(d["label"] is None for d in decisions) or "needs_context" in blocking:
        status = "ambiguous"
    elif candidate is None:
        status = "tie"
    else:
        status = "review_required" if flags else "auto_accepted"
    origin = "moderation_tiebreak" if (is_tie and mod_decision and final is not None) else "model_consensus" if final is not None else "unresolved"
    result = {**record, "run_id": run_id, "label": candidate, "candidate_label": candidate, "final_label": final,
              "positive_votes": positives, "negative_votes": negatives, "valid_binary_votes": positives + negatives,
              "needs_review": bool(flags), "blocking_review": bool(blocking), "status": status,
              "review_flags": sorted(flags), "abuse_types_union": sorted({t for d in decisions for t in d["abuse_types"]}),
              "discourse_tags_union": tags, "label_origin": origin,
              "tag_annotation_origin": "model_only",
              "quality_tier": "silver" if final is not None else "unresolved", "human_label": None,
              "reviewer": "", "review_note": "", "votes": votes,
              "moderation": mod_decision}
    if review:
        result.update(final_label=review["human_label"], human_label=review["human_label"], reviewer=review["reviewer"],
                      review_note=review["review_note"], status="human_reviewed", needs_review=False, blocking_review=False,
                      label_origin="human_review", quality_tier="human_reviewed")
    return result
