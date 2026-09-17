"""LLM access layer: Ollama Cloud (primary) with Groq as automatic fallback, or the reverse.

Every agent calls `structured_call(role, Schema, system, user)` and gets back a validated
Pydantic object plus token usage. What sits behind it:

- Routing by *role*: volume work (claim extraction) uses the small model, judgement work
  (planning, verification, answering) the large one. The fact checker therefore never runs
  on the same model as the extractor.
- Ollama Cloud: a pool of API keys (OLLAMA_API_KEY_1..n) used round-robin with one request in
  flight per key, so the extraction fan-out really runs in parallel. A key that reports quota,
  auth or rate problems is retired for a while and the next one is used.
  Ollama's JSON-schema output is best-effort, so the response is validated with Pydantic and
  retried once with the validation error fed back.
- Groq: strict JSON schema, but ~8K tokens/min per model on the free tier, enforced here with a
  per-model sliding-window TokenBudget.
- If the primary provider fails outright, the call is retried on the other provider.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings
from .keys import KeyRing, NoKeyAvailable, numbered_env

log = logging.getLogger(__name__)

Role = Literal["planner", "extractor", "checker", "answer"]
T = TypeVar("T", bound=BaseModel)

try:  # gpt-oss uses the o200k vocabulary; tiktoken has it
    import tiktoken
    _enc = tiktoken.get_encoding("o200k_base")

    def estimate_tokens(text: str) -> int:
        return len(_enc.encode(text))
except Exception:  # noqa: BLE001
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)


class LLMParseError(RuntimeError):
    pass


class LLMUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------- routing
_OLLAMA_KEYS = numbered_env("OLLAMA_API_KEY")


def _providers() -> list[str]:
    have = {"ollama": bool(_OLLAMA_KEYS), "groq": bool(settings.groq_api_key)}
    order = [settings.llm_provider] + [p for p in ("ollama", "groq") if p != settings.llm_provider]
    return [p for p in order if have[p]]


def model_for(role: Role, provider: str | None = None) -> str:
    provider = provider or (_providers() or [settings.llm_provider])[0]
    return getattr(settings, f"{provider}_model_{role}")


# --------------------------------------------------------------------------- usage accounting
_usage: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
_usage_lock = threading.Lock()


def _record(role: str, provider: str, prompt: int, completion: int) -> None:
    with _usage_lock:
        for k in (role, f"provider:{provider}"):
            u = _usage[k]
            u["calls"] += 1
            u["prompt_tokens"] += prompt or 0
            u["completion_tokens"] += completion or 0


def usage_snapshot() -> dict[str, dict[str, int]]:
    with _usage_lock:
        return {k: dict(v) for k, v in _usage.items()}


# --------------------------------------------------------------------------- Ollama Cloud
_ollama_ring = KeyRing("ollama", _OLLAMA_KEYS, settings.ollama_per_key_concurrency) if _OLLAMA_KEYS else None
_ollama_http = httpx.Client(base_url=settings.ollama_base_url, timeout=httpx.Timeout(120, connect=15))


def _inline_schema(schema: dict) -> dict:
    """Resolve $ref/$defs and drop titles so the schema is compact and self-contained in a prompt."""
    defs = schema.get("$defs", {})

    def walk(n):
        if isinstance(n, dict):
            if "$ref" in n:
                return walk(copy.deepcopy(defs[n["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in n.items() if k not in ("$defs", "title")}
        if isinstance(n, list):
            return [walk(x) for x in n]
        return n
    return walk(schema)


def _strip_fences(text: str) -> str:
    t = text.strip()
    a, b = t.find("{"), t.rfind("}")
    return t[a:b + 1] if a >= 0 and b > a else t


def _ollama_once(model: str, messages: list[dict], max_tokens: int, effort: str) -> dict:
    body = {
        "model": model, "messages": messages, "stream": False,
        # gpt-oss takes a reasoning level; other models take a boolean
        "think": effort if model.startswith("gpt-oss") else False,
        "options": {"temperature": 0, "num_predict": max_tokens * 2},   # headroom for reasoning tokens
    }
    assert _ollama_ring is not None
    last = "no attempt"
    for _ in range(len(_ollama_ring) + 1):
        with _ollama_ring.lease() as key:
            try:
                r = _ollama_http.post("/api/chat", json=body, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                _ollama_ring.retire(key, 20, last[:60])
                continue
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:120]}"
            if r.status_code in (401, 402, 403):
                _ollama_ring.retire(key, 3600, last)          # bad key / out of allowance
            elif r.status_code == 429:
                _ollama_ring.retire(key, 90, last)            # session limit: try another key
            elif r.status_code >= 500:
                _ollama_ring.retire(key, 10, last)
            else:
                raise LLMUnavailable(last)
    raise LLMUnavailable(f"ollama: {last}")


def _call_ollama(role: Role, schema: type[T], system: str, user: str, max_tokens: int, effort: str) -> tuple[T, dict]:
    model = model_for(role, "ollama")
    # gpt-oss on Ollama Cloud ignores the `format` constraint (verified: it returns fenced JSON with
    # invented keys) and tool-calling drops required fields. Putting the inlined JSON Schema in the
    # prompt and validating with Pydantic is the variant that validated reliably in testing.
    js = json.dumps(_inline_schema(schema.model_json_schema()), separators=(",", ":"))
    messages = [{"role": "system", "content": system + "\n\nReturn ONLY a JSON object (no markdown fences, no commentary) "
                                                       "that validates against this JSON Schema:\n" + js},
                {"role": "user", "content": user}]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    err: Exception | None = None
    for attempt in range(2):
        t0 = time.time()
        j = _ollama_once(model, messages, max_tokens, effort)
        p, c = j.get("prompt_eval_count") or 0, j.get("eval_count") or 0
        usage = {"prompt_tokens": usage["prompt_tokens"] + p, "completion_tokens": usage["completion_tokens"] + c,
                 "total_tokens": usage["total_tokens"] + p + c}
        _record(role, "ollama", p, c)
        content = (j.get("message") or {}).get("content") or ""
        log.info("llm %s/ollama:%s %.1fs tokens=%d", role, model, time.time() - t0, p + c)
        try:
            data = json.loads(_strip_fences(content))
            if isinstance(data, str):            # occasionally double-encoded
                data = json.loads(data)
            return schema.model_validate(data), usage
        except (json.JSONDecodeError, ValidationError) as e:
            err = e
            messages = messages[:2] + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": f"That did not validate against the schema:\n{str(e)[:800]}\n"
                                            "Return the corrected JSON only."}]
    raise LLMParseError(f"{role}: {err}")


# --------------------------------------------------------------------------- Groq
class TokenBudget:
    """Sliding 60 s window of token reservations per model."""

    def __init__(self, tpm: int):
        self.tpm = tpm
        self._win: dict[str, deque[list]] = defaultdict(deque)   # entries: [t, tokens]
        self._cv = threading.Condition()

    def _used(self, model: str, now: float) -> int:
        q = self._win[model]
        while q and now - q[0][0] > 60:
            q.popleft()
        return sum(e[1] for e in q)

    def acquire(self, model: str, est: int) -> list:
        est = min(est, self.tpm)
        with self._cv:
            while True:
                now = time.monotonic()
                used = self._used(model, now)
                if used + est <= self.tpm:
                    ticket = [now, est]
                    self._win[model].append(ticket)
                    return ticket
                oldest = self._win[model][0][0] if self._win[model] else now
                wait = max(0.5, 60 - (now - oldest) + 0.1)
                log.info("token budget: %s used %d/%d, waiting %.1fs", model, used, self.tpm, wait)
                self._cv.wait(timeout=wait)

    def settle(self, ticket: list, actual: int | None) -> None:
        if actual is None:
            return
        with self._cv:
            ticket[1] = actual
            self._cv.notify_all()


_budget = TokenBudget(settings.groq_tpm_budget)
_groq_cache: dict[tuple, object] = {}


def _call_groq(role: Role, schema: type[T], system: str, user: str, max_tokens: int, effort: str) -> tuple[T, dict]:
    from langchain_groq import ChatGroq
    model = model_for(role, "groq")
    ticket = _budget.acquire(model, estimate_tokens(system) + estimate_tokens(user) + max_tokens)
    key = (model, max_tokens, effort)
    if key not in _groq_cache:
        _groq_cache[key] = ChatGroq(model=model, api_key=settings.groq_api_key, temperature=0, max_tokens=max_tokens,
                                    reasoning_effort=effort, max_retries=settings.groq_max_retries, timeout=90)
    runnable = _groq_cache[key].with_structured_output(schema, method="json_schema", strict=True, include_raw=True)
    t0 = time.time()
    try:
        out = runnable.invoke([("system", system), ("user", user)])
    except Exception:
        _budget.settle(ticket, None)
        raise
    usage = (getattr(out["raw"], "response_metadata", {}) or {}).get("token_usage") or {}
    _budget.settle(ticket, usage.get("total_tokens"))
    _record(role, "groq", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    log.info("llm %s/groq:%s %.1fs tokens=%s", role, model, time.time() - t0, usage.get("total_tokens"))
    if out.get("parsed") is None:
        raise LLMParseError(f"{role}: {out.get('parsing_error')}")
    return out["parsed"], usage


# --------------------------------------------------------------------------- public API
def structured_call(role: Role, schema: type[T], system: str, user: str, *,
                    max_tokens: int = 1200, reasoning_effort: str = "low") -> tuple[T, dict]:
    """One structured LLM call. Tries the primary provider, then the fallback."""
    providers = _providers()
    if not providers:
        raise LLMUnavailable("no LLM provider configured: set OLLAMA_API_KEY_1 or GROQ_API_KEY")
    last: Exception | None = None
    for i, provider in enumerate(providers):
        try:
            fn = _call_ollama if provider == "ollama" else _call_groq
            return fn(role, schema, system, user, max_tokens, reasoning_effort)
        except (LLMUnavailable, NoKeyAvailable, httpx.HTTPError) as e:
            last = e
            log.warning("llm provider %s unavailable for %s: %s%s", provider, role, e,
                        " - falling back" if i + 1 < len(providers) else "")
        except LLMParseError as e:
            last = e
            if i + 1 == len(providers):
                raise
            log.warning("llm provider %s could not produce valid JSON for %s - falling back: %s",
                        provider, role, str(e)[:300].replace("\n", " "))
    raise LLMUnavailable(str(last))
