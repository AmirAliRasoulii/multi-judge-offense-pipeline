import json
import math
import re
from .common import dumps

ENUMS = {
    "abuse_types": ["profanity_use", "insult", "derogatory_mockery", "hate_speech"],
    "target_types": ["individual", "group_identity", "other_group", "self", "none", "unclear"],
    "target_basis": ["ethnicity", "nationality", "race", "religion", "gender", "sexual_orientation", "disability", "age", "caste", "political_affiliation", "profession", "other_identity", "not_identity", "none", "unknown"],
    "discourse_tags": ["quotation", "reported_speech", "educational", "counterspeech", "reclaimed", "humor", "criticism", "untargeted_profanity", "code_switching", "obfuscated"],
    "expression": ["explicit", "implicit", "mixed", "none", "unclear"],
    "speaker_stance": ["attack", "endorse", "reject", "report", "explain", "neutral", "unclear"],
}
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "label": {"type": ["integer", "null"], "enum": [0, 1, None]},
    "p_offensive_raw": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    **{k: {"type": "array", "items": {"type": "string", "enum": v}} for k, v in ENUMS.items() if k.endswith("types") or k in ("discourse_tags", "target_basis")},
    **{k: {"type": "string", "enum": v} for k, v in ENUMS.items() if k in ("expression", "speaker_stance")},
    "profanity_present": {"type": "boolean"}, "needs_context": {"type": "boolean"},
    "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": ["abuse", "usage_context", "target"]}},
        "required": ["text", "kind"]}},
    "decision_reason": {"type": "string"},
}}
SCHEMA["required"] = list(SCHEMA["properties"])


class InvalidResponse(ValueError):
    pass


def validate(value):
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise InvalidResponse("JSON fields do not match schema")
    label = value["label"]
    if label is not None and (type(label) is not int or label not in (0, 1)):
        raise InvalidResponse("label must be integer 0, 1 or null")
    p = value["p_offensive_raw"]
    if p is not None and (type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1):
        raise InvalidResponse("p_offensive_raw must be a finite number in [0,1]")
    if (label is None) != (p is None):
        raise InvalidResponse("Unknown label requires null probability, known label requires probability")
    for name, allowed in ENUMS.items():
        item = value[name]
        if name in ("expression", "speaker_stance"):
            if not isinstance(item, str) or item not in allowed:
                raise InvalidResponse(f"Invalid {name}")
        elif not isinstance(item, list) or any(not isinstance(x, str) or x not in allowed for x in item) or len(set(item)) != len(item):
            raise InvalidResponse(f"Invalid {name}")
    for name in ("profanity_present", "needs_context"):
        if type(value[name]) is not bool:
            raise InvalidResponse(f"{name} must be boolean")
    reason = value["decision_reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 400:
        raise InvalidResponse("decision_reason must contain 1..400 characters")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 12:
        raise InvalidResponse("Invalid evidence array")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"text", "kind"} or item["kind"] not in ("abuse", "usage_context", "target") or not isinstance(item["text"], str) or not item["text"] or len(item["text"]) > 500:
            raise InvalidResponse("Invalid evidence item")
    return value


def parse_content(content):
    if not isinstance(content, str) or not content.strip():
        raise InvalidResponse("Empty textual response")
    # Preserve raw content elsewhere; do not parse objects from private think blocks.
    if "<think>" in content and "</think>" not in content:
        raise InvalidResponse("Unclosed think block")
    visible = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    marker = "FINAL_JSON:"
    if marker in visible:
        prefix, body = visible.rsplit(marker, 1)
    else:
        prefix, body = "", visible
    # Scan top-level objects; skip nested objects after successful decoding.
    candidates, cursor = [], 0
    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise InvalidResponse("Duplicate JSON key")
            obj[key] = value
        return obj
    decoder = json.JSONDecoder(object_pairs_hook=unique_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(InvalidResponse("Non-finite JSON number")))
    while cursor < len(body):
        start = body.find("{", cursor)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(body, start)
        except (json.JSONDecodeError, InvalidResponse):
            cursor = start + 1
            continue
        cursor = end
        if isinstance(obj, dict) and "label" in obj:
            candidates.append((obj, start, end))
    if len(candidates) != 1:
        raise InvalidResponse("Expected exactly one decision JSON object")
    obj, start, end = candidates[0]
    # Extra JSON after a decision is ambiguous, even if it has no label.
    suffix = body[end:].strip().strip("`").strip()
    if suffix:
        raise InvalidResponse("Unexpected content after decision JSON")
    validate(obj)
    before = prefix.strip() if marker in visible else body[:start].replace("```json", "").replace("```", "").strip()
    return obj, before


def _normalize_persian(text):
    if not isinstance(text, str):
        return ""
    t = text.replace("ك", "ک").replace("ي", "ی").replace("ى", "ی").replace("ـ", "")
    return re.sub(r"[\s\u200c]+", " ", t).strip()


def decision_warnings(decision, record):
    warnings, locations = [], []
    label, p = decision["label"], decision["p_offensive_raw"]
    if label is not None and ((label == 1 and p < .5) or (label == 0 and p > .5)):
        warnings.append("score_label_conflict")
    if (label == 0 and decision["abuse_types"]) or (label == 1 and not decision["abuse_types"]):
        warnings.append("label_tag_conflict")
    for e in decision["evidence"]:
        hits = []
        for field in ("text", "context"):
            source = record.get(field, "")
            start = source.find(e["text"])
            if start >= 0:
                hits.append({"field": field, "start": start, "end": start + len(e["text"])})
            else:
                norm_source = _normalize_persian(source)
                norm_evidence = _normalize_persian(e["text"])
                n_start = norm_source.find(norm_evidence) if norm_evidence else -1
                if n_start >= 0:
                    hits.append({"field": field, "start": n_start, "end": n_start + len(e["text"]), "normalized_match": True})
        if not hits:
            warnings.append("evidence_not_found")
        locations.append({**e, "matches": hits})
    if label == 1 and not any(e["kind"] == "abuse" and any(h["field"] == "text" for h in e["matches"]) for e in locations):
        warnings.append("missing_target_text_abuse_evidence")
    return sorted(set(warnings)), locations


def system_prompt(config, model):
    output = ("پیش از JSON فقط یک جمله دلیل کوتاه بده، سپس خط FINAL_JSON: و بعد یک شیء JSON معتبر. هیچ متن دیگری پس از آن نده."
              if model.output_mode == "reasoning_json" else "فقط یک شیء JSON بده. توضیح کوتاه را فقط در decision_reason بنویس.")
    return config.prompt + "\n\n" + output + "\nJSON schema:\n" + dumps(SCHEMA)
