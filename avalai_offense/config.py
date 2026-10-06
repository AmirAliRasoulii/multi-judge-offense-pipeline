import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Model:
    slot: int
    id: str
    api: str = "chat"
    output_mode: str = "reasoning_json"
    max_tokens: int = 4096
    token_field: str = "max_completion_tokens"
    reasoning_effort: str = ""
    extra: dict = None


@dataclass
class Config:
    key: str
    base_url: str
    models: list
    prompt: str
    policy: dict
    workers: int = 4
    timeout: float = 120
    retries: int = 2
    rpm: float = 0
    max_calls: int = 0
    moderation_model: str = "omni-moderation-latest"
    network_mode: str = "direct"
    proxy_url: str = ""
    bind_ip: str = ""

    def public(self):
        return {"base_url": self.base_url, "models": [asdict(m) for m in self.models],
                "prompt": self.prompt, "policy": self.policy, "moderation_model": self.moderation_model,
                "network_mode": self.network_mode, "version": "0.1.0"}


def env_values(path):
    values = {}
    path = Path(path)
    if path.exists():
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                raise ValueError(f"Invalid .env line {n}")
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            values[k.strip()] = v
    values.update(os.environ)
    return values


def connection(values):
    url = values.get("AVALAI_BASE_URL", "https://api.avalai.ir/v1").rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("AVALAI_BASE_URL must be an HTTPS URL without credentials/query")
    return values.get("AVALAI_API_KEY", ""), url


def load_config(path=".env", require_key=True):
    values = env_values(path)
    key, base = connection(values)
    if require_key and not key:
        raise ValueError("Set AVALAI_API_KEY in .env")
    root = Path(path).resolve().parent
    prompt = (root / values.get("PROMPT_FILE", "prompts/classifier.md")).read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValueError("Prompt is empty")
    policy = json.loads((root / values.get("REVIEW_POLICY_FILE", "config/review_policy.json")).read_text())
    for name in ("min_vote_confidence_raw", "audit_unanimous_rate"):
        if type(policy.get(name)) not in (int, float) or not 0 <= policy[name] <= 1:
            raise ValueError(f"Invalid policy: {name}")
    for name in ("review_minority_vote", "block_minority_vote", "block_sensitive_discourse", "review_subtype_disagreement", "block_subtype_disagreement"):
        if type(policy.get(name)) is not bool:
            raise ValueError(f"Invalid boolean policy: {name}")
    from .schema import ENUMS
    tags = policy.get("sensitive_discourse_tags")
    if not isinstance(tags, list) or any(t not in ENUMS["discourse_tags"] for t in tags):
        raise ValueError("Invalid sensitive_discourse_tags")
    models = []
    forbidden = {"model", "messages", "input", "instructions", "stream", "response_format", "text",
                 "max_tokens", "max_completion_tokens", "max_output_tokens", "reasoning", "reasoning_effort",
                 "api_key", "authorization", "headers"}
    for slot in range(1, 5):
        prefix = f"MODEL_{slot}"
        m = Model(slot, values.get(prefix, "").strip(), values.get(prefix + "_API", "chat"),
                  values.get(prefix + "_OUTPUT_MODE", "reasoning_json"), int(values.get(prefix + "_MAX_TOKENS", 4096)),
                  values.get(prefix + "_TOKEN_FIELD", "max_completion_tokens"),
                  values.get(prefix + "_REASONING_EFFORT", ""), json.loads(values.get(prefix + "_EXTRA_JSON", "{}")))
        if not m.id or m.api not in ("chat", "responses") or m.output_mode not in ("reasoning_json", "json_object", "json_schema"):
            raise ValueError(f"Invalid settings for {prefix}")
        if m.max_tokens < 256 or m.token_field not in ("max_tokens", "max_completion_tokens"):
            raise ValueError(f"Invalid token settings for {prefix}")
        if not isinstance(m.extra, dict) or forbidden.intersection(k.lower() for k in m.extra):
            raise ValueError(f"Protected or invalid EXTRA_JSON for {prefix}")
        models.append(m)
    if len({m.id for m in models}) != 4:
        raise ValueError("Four distinct model IDs are required")
    cfg = Config(key, base, models, prompt, policy, int(values.get("MAX_WORKERS", 4)),
                 float(values.get("REQUEST_TIMEOUT", 120)), int(values.get("MAX_RETRIES", 2)),
                 float(values.get("REQUESTS_PER_MINUTE", 0)), int(values.get("MAX_API_CALLS", 0)),
                 values.get("MODERATION_MODEL", "omni-moderation-latest").strip(),
                 values.get("NETWORK_MODE", "direct").strip().lower(),
                 values.get("PROXY_URL", "").strip(),
                 values.get("BIND_IP", "").strip())
    if cfg.workers < 1 or cfg.timeout <= 0 or cfg.retries < 0 or cfg.rpm < 0 or cfg.max_calls < 0:
        raise ValueError("Invalid execution limits")
    return cfg
