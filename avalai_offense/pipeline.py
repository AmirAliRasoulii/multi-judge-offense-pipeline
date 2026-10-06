import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from .aggregation import aggregate, cache_key
from .common import digest, now
from .config import Config, Model
from .provider import APIError, AvalAI, BudgetExceeded, extract_content
from .schema import InvalidResponse, decision_warnings, parse_content
from .storage import RunLock, Store


def judge(store, config, provider, model, record, retry_failed=False):
    key = cache_key(config, model, record)
    existing = store.get_vote(key)
    if existing and (existing["status"] == "ok" or not retry_failed):
        return {**existing, "cache_hit": True}
    base = {"slot": model.slot, "model": model.id, "api": model.api, "cache_key": key,
            "cache_hit": False, "status": "error", "decision": None, "warnings": [], "error": "",
            "score_type": "self_reported", "tag_annotation_origin": "model_only"}
    for n in range(config.retries + 1):
        start = time.monotonic()
        attempt = {"started_at": now(), "model": model.id, "retry_index": n, "status": "error"}
        retry, wait = False, min(2**n, 15)
        try:
            raw = provider.call(model, record, repair=n > 0)
            attempt["raw_response"] = raw
            attempt["usage"] = raw.get("usage", {}) if isinstance(raw, dict) else {}
            content, provider_reasoning = extract_content(raw, model.api)
            attempt.update(content=content, provider_reasoning=provider_reasoning)
            decision, before = parse_content(content)
            warnings, evidence = decision_warnings(decision, record)
            base.update(status="ok", decision=decision, warnings=warnings, evidence_locations=evidence,
                        reasoning_before_json=before, response_id=raw.get("id"), usage=attempt["usage"], error="")
            attempt["status"] = "ok"
        except BudgetExceeded:
            # No request was made: do not cache a failure or invent a zero vote.
            raise
        except APIError as e:
            attempt.update(error=str(e), http_status=e.status, raw_error_response=e.raw)
            base["error"] = str(e)
            retry, wait = e.retryable, max(wait, e.retry_after)
            if e.status in (401, 403):
                attempt["elapsed_seconds"] = time.monotonic() - start
                store.attempt(key, model.slot, attempt)
                raise
        except InvalidResponse as e:
            attempt["error"] = base["error"] = str(e)
            retry = "refusal" not in str(e).lower()
        except Exception:
            # Programming errors must surface; do not silently convert them into dataset labels.
            raise
        attempt["elapsed_seconds"] = round(time.monotonic() - start, 3)
        base["last_attempt_id"] = store.attempt(key, model.slot, attempt)
        if base["status"] == "ok" or not retry or n == config.retries:
            break
        time.sleep(min(wait, 30))
    base["completed_at"] = now()
    store.set_vote(key, base)
    return base


def moderation_cache_key(config, record):
    return digest({"base_url": config.base_url, "model": config.moderation_model,
                   "text": record["text"], "type": "moderation", "version": "0.1.0"})


def judge_moderation(store, config, provider, record):
    if not getattr(config, "moderation_model", ""):
        return None
    key = moderation_cache_key(config, record)
    existing = store.get_vote(key)
    if existing and existing.get("status") == "ok":
        return {**existing, "cache_hit": True}
    base = {"slot": "moderation", "model": config.moderation_model, "api": "moderation",
            "cache_key": key, "cache_hit": False, "status": "error", "decision": None, "error": ""}
    start = time.monotonic()
    attempt = {"started_at": now(), "model": config.moderation_model, "type": "moderation", "status": "error"}
    try:
        raw = provider.call_moderation(record["text"], config.moderation_model)
        attempt["raw_response"] = raw
        from .provider import extract_moderation
        decision = extract_moderation(raw)
        base.update(status="ok", decision=decision, response_id=raw.get("id"), error="")
        attempt["status"] = "ok"
    except BudgetExceeded:
        raise
    except APIError as e:
        attempt.update(error=str(e), http_status=e.status, raw_error_response=e.raw)
        base["error"] = str(e)
        if e.status in (401, 403):
            attempt["elapsed_seconds"] = time.monotonic() - start
            store.attempt(key, 0, attempt)
            raise
    except Exception as e:
        attempt["error"] = base["error"] = str(e)
    attempt["elapsed_seconds"] = round(time.monotonic() - start, 3)
    base["last_attempt_id"] = store.attempt(key, 0, attempt)
    base["completed_at"] = now()
    store.set_vote(key, base)
    return base


def run_pipeline(records, config, output, provider=None, retry_failed=False, progress=print):
    with RunLock(output):
        store = Store(output)
        try:
            manifest = store.init_run(records, config)
            provider = provider or AvalAI(config)
            stopped = None
            with ThreadPoolExecutor(max_workers=config.workers) as pool:
                for index, record in enumerate(records, 1):
                    futures = [pool.submit(judge, store, config, provider, model, record, retry_failed) for model in config.models]
                    record_votes = []
                    for future in futures:
                        if future.cancelled():
                            continue
                        try:
                            record_votes.append(future.result())
                        except (BudgetExceeded, APIError) as e:
                            stopped = str(e)
                            for pending in futures:
                                pending.cancel()
                    if stopped:
                        break
                    # If 4 judges completed and resulted in a 2-2 tie, query moderation model
                    ok_decisions = [v.get("decision") for v in record_votes if v.get("status") == "ok" and v.get("decision")]
                    if len(ok_decisions) == 4 and all(d.get("label") is not None for d in ok_decisions):
                        pos = sum(d["label"] == 1 for d in ok_decisions)
                        neg = sum(d["label"] == 0 for d in ok_decisions)
                        if pos == 2 and neg == 2 and getattr(config, "moderation_model", ""):
                            try:
                                judge_moderation(store, config, provider, record)
                            except (BudgetExceeded, APIError) as e:
                                stopped = str(e)
                                break
                    if index == 1 or index % 10 == 0 or index == len(records):
                        progress(f"Annotated {index}/{len(records)} records; completed votes are checkpointed.")
            # Pool drained: successful in-flight votes are saved before exporting.
            results = collect_results(store, config, records, manifest)
            from .export import export_run
            summary = export_run(store, results, manifest)
            if stopped:
                progress(f"Stopped safely: {stopped}. Export includes unresolved records; rerun to resume.")
            return summary, stopped
        finally:
            store.close()


def collect_results(store, config, records, manifest):
    reviews = store.reviews()
    results, duplicate_seen = [], {}
    mod_model = getattr(config, "moderation_model", "")
    for record in records:
        votes = []
        for model in config.models:
            key = cache_key(config, model, record)
            v = store.get_vote(key)
            votes.append(v or {"slot": model.slot, "model": model.id, "api": model.api, "cache_key": key,
                               "status": "pending", "decision": None, "warnings": [], "error": "Not completed"})
        mod_vote = None
        if mod_model:
            mod_key = moderation_cache_key(config, record)
            mod_vote = store.get_vote(mod_key)
        result = aggregate(record, votes, config.policy, manifest["run_id"], reviews.get(record["id"]), moderation=mod_vote)
        first = duplicate_seen.setdefault(record["content_hash"], record["id"])
        result["duplicate_of"] = first if first != record["id"] else ""
        results.append(result)
    return results


def load_run(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    public = manifest["configuration"]
    config = Config("", public["base_url"], [Model(**m) for m in public["models"]], public["prompt"], public["policy"],
                    moderation_model=public.get("moderation_model", ""))
    records = [json.loads(line) for line in (directory / "input_snapshot.jsonl").read_text().splitlines() if line]
    if digest(records) != manifest["input_hash"]:
        raise ValueError("Input snapshot was modified")
    return config, records, manifest


def export_existing(directory):
    with RunLock(directory):
        config, records, manifest = load_run(directory)
        store = Store(directory)
        try:
            from .export import export_run
            return export_run(store, collect_results(store, config, records, manifest), manifest)
        finally:
            store.close()
