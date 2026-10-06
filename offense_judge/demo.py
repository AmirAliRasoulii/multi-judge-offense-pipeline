"""Deterministic fixtures for exercising software; never a model-quality benchmark."""
import json
from pathlib import Path
from .common import atomic_text, dumps
from .config import Config, Model
from .inputs import read_records
from .pipeline import run_pipeline
from .provider import APIError, Gate

ROOT = Path(__file__).resolve().parent.parent

SAMPLES = [
    {"id": "fa-01", "text": "این استدلال ضعیف است و شواهد کافی ندارد.", "language": "fa", "case": "criticism"},
    {"id": "fa-02", "text": "تو احمقی و چیزی نمی‌فهمی.", "language": "fa", "case": "insult"},
    {"id": "fa-03", "text": "او گفت «تو احمقی»؛ این نوع صحبت را قبول ندارم.", "language": "fa", "case": "quotation"},
    {"id": "fa-04", "text": "واژهٔ «احمق» به چه معنی است؟", "language": "fa", "case": "educational"},
    {"id": "fa-05", "text": "چه نابغه‌ای! باز هم همه‌چیز را خراب کردی.", "language": "fa", "case": "tie"},
    {"id": "fa-06", "text": "لعنتی، اتوبوس رفت!", "language": "fa", "case": "untargeted"},
    {"id": "fa-07", "text": "خیلی خوب بود واقعاً!", "context": "پیامی پس از یک شکست؛ رابطه و لحن روشن نیست.", "language": "fa", "case": "uncertain"},
    {"id": "en-01", "text": "I disagree with your proposal; the evidence is weak.", "language": "en", "case": "criticism"},
    {"id": "en-02", "text": "You are an idiot.", "language": "en", "case": "minority"},
    {"id": "en-03", "text": "A normal greeting: hello, everyone.", "language": "en", "case": "error"},
    {"id": "fa-08", "text": "این استدلال ضعیف است و شواهد کافی ندارد.", "language": "fa", "case": "criticism"},
    {"id": "fa-09", "text": "=SUM(1,2) یک عبارت برای بررسی ذخیره امن متن است.", "language": "fa", "case": "criticism"},
]


def fixture_decision(record, slot):
    case = record["metadata"].get("case", "criticism")
    label = 1 if case in ("insult", "untargeted", "minority") else 0
    if case == "tie":
        label = 1 if slot <= 2 else 0
    if case == "minority" and slot == 4:
        label = 0
    tags = [case] if case in ("quotation", "educational", "criticism") else []
    if case == "untargeted":
        tags.append("untargeted_profanity")
    abuse = ["profanity_use" if case == "untargeted" else "derogatory_mockery" if case == "tie" else "insult"] if label == 1 else []
    return {"label": label, "p_offensive_raw": .96 if label else .04, "abuse_types": abuse,
            "target_types": ["none" if case == "untargeted" or label == 0 else "individual"],
            "target_basis": ["none" if label == 0 or case == "untargeted" else "not_identity"], "discourse_tags": tags,
            "expression": "explicit" if label else "none", "speaker_stance": "attack" if label else "explain" if case == "educational" else "reject" if case == "quotation" else "neutral",
            "profanity_present": case in ("insult", "untargeted", "quotation", "educational", "minority"),
            "needs_context": case == "uncertain", "evidence": [{"text": record["text"], "kind": "abuse" if label else "usage_context"}],
            "decision_reason": "رأی ساختگی برای نمایش رفتار نرم‌افزار؛ این خروجی مدل واقعی نیست."}


class DemoProvider:
    def __init__(self):
        self.gate = Gate()

    def call(self, model, record, repair=False):
        self.gate.acquire()
        if record["metadata"].get("case") == "error" and model.slot == 4:
            raise APIError("Demo HTTP 400 (intentional failure)", status=400, raw="Offline simulated failure")
        decision = fixture_decision(record, model.slot)
        if record["metadata"].get("case") == "uncertain":
            decision["p_offensive_raw"] = .45
        return {"id": "demo-response", "choices": [{"finish_reason": "stop", "message": {
            "content": "این توضیح کوتاه صرفاً نمونهٔ ساختگی است.\nFINAL_JSON:\n" + dumps(decision)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "demo": True}

    def call_moderation(self, text, model="omni-moderation-latest"):
        self.gate.acquire()
        flagged = any(w in text for w in ("احمق", "لعنتی", "idiot", "خراب کردی"))
        return {
            "id": "modr-demo",
            "model": model or "omni-moderation-latest",
            "results": [{
                "flagged": flagged,
                "categories": {"harassment": flagged, "hate": False},
                "category_scores": {"harassment": 0.85 if flagged else 0.04, "hate": 0.01}
            }],
            "demo": True
        }


def demo_config():
    cfg = Config("", "https://openrouter.ai/api/v1", [Model(i, f"DEMO-NOT-A-REAL-MODEL-{i}", extra={}) for i in range(1, 5)],
                 (ROOT / "prompts/classifier.md").read_text(), json.loads((ROOT / "config/review_policy.json").read_text()),
                 moderation_model="")
    cfg.policy["audit_unanimous_rate"] = 0
    return cfg


def run_demo(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "demo_input.jsonl"
    atomic_text(path, "".join(dumps({"source": "offline_software_demo", **r}) + "\n" for r in SAMPLES))
    config = demo_config()
    config.prompt = "DEMO ONLY: outputs are fixtures, not real annotations.\n" + config.prompt
    summary, stopped = run_pipeline(read_records(path), config, output, DemoProvider())
    return {**summary, "DEMO_ONLY": True, "real_api_calls": 0}
