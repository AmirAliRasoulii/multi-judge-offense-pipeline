import http.client
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from urllib import error, request
from urllib.parse import urlsplit
from .common import dumps
from .schema import SCHEMA, InvalidResponse, system_prompt


class APIError(Exception):
    def __init__(self, message, status=None, retryable=False, raw=None, retry_after=0):
        super().__init__(message)
        self.status, self.retryable, self.raw, self.retry_after = status, retryable, raw, retry_after


class BudgetExceeded(Exception):
    pass


class Gate:
    def __init__(self, rpm=0, max_calls=0):
        self.rpm, self.max_calls, self.calls = rpm, max_calls, 0
        self.lock = threading.Lock()
        self.next_at = 0

    def acquire(self):
        with self.lock:
            if self.max_calls and self.calls >= self.max_calls:
                raise BudgetExceeded("MAX_API_CALLS reached; resume in a new invocation")
            self.calls += 1
            wait = max(0, self.next_at - time.monotonic())
            self.next_at = max(self.next_at, time.monotonic()) + (60 / self.rpm if self.rpm else 0)
        while wait > 0:
            step = min(wait, 1)
            time.sleep(step)
            wait -= step


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer credential to an unexpected host.
        return None


class BoundHTTPSHandler(request.HTTPSHandler):
    def __init__(self, source_ip="", **kwargs):
        super().__init__(**kwargs)
        self.source_ip = source_ip

    def https_open(self, req):
        source_addr = (self.source_ip, 0) if self.source_ip else None
        return self.do_open(
            lambda host, **kw: http.client.HTTPSConnection(host, source_address=source_addr, **kw),
            req,
        )


def socks5_tunnel(proxy_host, proxy_port, dest_host, dest_port, timeout=120):
    s = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    s.sendall(b"\x05\x01\x00")
    resp = s.recv(2)
    if resp != b"\x05\x00":
        s.close()
        raise ValueError("SOCKS5 proxy authentication failed")
    host_bytes = dest_host.encode("utf-8")
    req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + dest_port.to_bytes(2, "big")
    s.sendall(req)
    rep = s.recv(4)
    if rep[1] != 0:
        s.close()
        raise ValueError(f"SOCKS5 proxy connection error code: {rep[1]}")
    if rep[3] == 1:
        s.recv(4 + 2)
    elif rep[3] == 3:
        l = s.recv(1)[0]
        s.recv(l + 2)
    elif rep[3] == 4:
        s.recv(16 + 2)
    ctx = ssl.create_default_context()
    return ctx.wrap_socket(s, server_hostname=dest_host)


class SOCKS5HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port=443, proxy_host="127.0.0.1", proxy_port=10808, timeout=120, **kwargs):
        super().__init__(host, port, timeout=timeout, **kwargs)
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port

    def connect(self):
        self.sock = socks5_tunnel(self.proxy_host, self.proxy_port, self.host, self.port, timeout=self.timeout)


class SOCKS5HTTPSHandler(request.HTTPSHandler):
    def __init__(self, proxy_host, proxy_port, **kwargs):
        super().__init__(**kwargs)
        self.proxy_host, self.proxy_port = proxy_host, proxy_port

    def https_open(self, req):
        return self.do_open(
            lambda host, **kw: SOCKS5HTTPSConnection(host, proxy_host=self.proxy_host, proxy_port=self.proxy_port, **kw),
            req,
        )


def parse_proxy_url(proxy_url):
    parsed = urlsplit(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (10808 if "socks" in parsed.scheme else 8080)
    return parsed.scheme.lower(), host, port


def get_direct_ip():
    env_ip = os.environ.get("BIND_IP", "").strip()
    if env_ip:
        return env_ip
    try:
        output = subprocess.check_output(["ip", "route", "show", "table", "main"], timeout=1, text=True)
        for line in output.splitlines():
            if line.startswith("default ") and "src " in line and "tun" not in line:
                parts = line.split()
                if "src" in parts:
                    return parts[parts.index("src") + 1]
    except Exception:
        pass
    return ""


def make_opener(network_mode="direct", proxy_url="", bind_ip=""):
    handlers = [NoRedirect()]
    mode = (network_mode or "direct").lower()
    if mode == "proxy" and proxy_url:
        scheme, host, port = parse_proxy_url(proxy_url)
        if scheme.startswith("socks"):
            handlers.append(SOCKS5HTTPSHandler(host, port))
        else:
            handlers.append(request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    elif mode == "direct":
        ip = bind_ip or get_direct_ip()
        if ip:
            handlers.append(BoundHTTPSHandler(ip))
    return request.build_opener(*handlers)


def fetch_json(url, key="", payload=None, timeout=120, opener=None, extra_headers=None):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if extra_headers:
        headers.update(extra_headers)
    req = request.Request(url, data=dumps(payload).encode() if payload is not None else None, headers=headers)
    if opener is None:
        opener = make_opener()
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise APIError("API returned non-JSON", retryable=True, raw=raw.replace(key, "[REDACTED]") if key else raw) from e
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        if key:
            raw = raw.replace(key, "[REDACTED]")
        try:
            retry_after = min(30, max(0, float(e.headers.get("Retry-After", 0))))
        except ValueError:
            retry_after = 0
        raise APIError(f"HTTP {e.code}", e.code, e.code in (408, 409, 429) or e.code >= 500, raw, retry_after) from e
    except (error.URLError, TimeoutError, socket.timeout) as e:
        raise APIError("Network error or timeout", retryable=True) from e


def build_payload(config, model, record, repair=False):
    user = dumps({"text": record["text"], "context": record["context"], "language": record["language"]})
    if repair:
        user += "\nپاسخ قبلی کامل یا مطابق قالب نبود. دوباره فقط با قالب خروجی مقرر پاسخ بده."
    system = system_prompt(config, model)
    schema_format = {"type": "json_schema", "json_schema": {"name": "offense_annotation", "strict": True, "schema": SCHEMA}}
    if model.api == "chat":
        payload = {"model": model.id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "stream": False, model.token_field: model.max_tokens}
        if model.reasoning_effort:
            payload["reasoning_effort"] = model.reasoning_effort
        if model.output_mode != "reasoning_json":
            payload["response_format"] = schema_format if model.output_mode == "json_schema" else {"type": "json_object"}
    else:
        payload = {"model": model.id, "instructions": system, "input": [{"role": "user", "content": user}],
                   "stream": False, "max_output_tokens": model.max_tokens}
        if model.reasoning_effort:
            payload["reasoning"] = {"effort": model.reasoning_effort}
        if model.output_mode != "reasoning_json":
            fmt = {"type": "json_schema", **schema_format["json_schema"]} if model.output_mode == "json_schema" else {"type": "json_object"}
            payload["text"] = {"format": fmt}
    payload.update(model.extra or {})
    return payload


def extract_content(raw, api):
    if not isinstance(raw, dict):
        raise InvalidResponse("API response is not an object")
    if raw.get("error"):
        raise InvalidResponse("API response contains an error")
    if api == "chat":
        choices = raw.get("choices") or []
        if len(choices) != 1:
            raise InvalidResponse("Expected one completion choice")
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise InvalidResponse("Model refusal")
        if choice.get("finish_reason") not in ("stop", "end_turn"):
            raise InvalidResponse("Incomplete or filtered completion: " + str(choice.get("finish_reason")))
        content = message.get("content")
        if isinstance(content, list):
            if any(p.get("type") == "refusal" for p in content):
                raise InvalidResponse("Model refusal")
            content = "".join(p.get("text", "") for p in content if p.get("type") in ("text", "output_text"))
        return content, message.get("reasoning_content") or message.get("reasoning") or ""
    if raw.get("status") != "completed" or raw.get("incomplete_details"):
        raise InvalidResponse("Incomplete Responses output")
    texts, reasoning = [], []
    for item in raw.get("output") or []:
        if item.get("type") == "reasoning":
            reasoning.append(item)
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise InvalidResponse("Model refusal")
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
    return "".join(texts) or raw.get("output_text", ""), reasoning


def extract_moderation(raw):
    if not isinstance(raw, dict):
        raise InvalidResponse("Moderation response is not an object")
    if raw.get("error"):
        raise InvalidResponse("Moderation response contains an error: " + str(raw.get("error")))
    results = raw.get("results") or []
    if not results or not isinstance(results[0], dict):
        raise InvalidResponse("Moderation results missing or empty")
    first = results[0]
    flagged = bool(first.get("flagged", False))
    categories = first.get("categories") or {}
    category_scores = first.get("category_scores") or {}
    flagged_categories = [k for k, v in categories.items() if v]
    return {
        "flagged": flagged,
        "label": 1 if flagged else 0,
        "flagged_categories": flagged_categories,
        "categories": categories,
        "category_scores": category_scores,
    }


class LLMProvider:
    def __init__(self, config):
        self.config = config
        self.gate = Gate(config.rpm, config.max_calls)
        self.opener = make_opener(
            getattr(config, "network_mode", "system"),
            getattr(config, "proxy_url", ""),
            getattr(config, "bind_ip", ""),
        )
        self.extra_headers = getattr(config, "extra_headers", None) or {}

    def call(self, model, record, repair=False):
        self.gate.acquire()
        endpoint = "/chat/completions" if model.api == "chat" else "/responses"
        return fetch_json(self.config.base_url + endpoint, self.config.key,
                          build_payload(self.config, model, record, repair), self.config.timeout,
                          opener=self.opener, extra_headers=self.extra_headers)

    def call_moderation(self, text, model="omni-moderation-latest"):
        self.gate.acquire()
        payload = {"input": text, "model": model or "omni-moderation-latest"}
        return fetch_json(self.config.base_url + "/moderations", self.config.key, payload,
                          self.config.timeout, opener=self.opener, extra_headers=self.extra_headers)


# Backward compatibility alias
AvalAI = LLMProvider
