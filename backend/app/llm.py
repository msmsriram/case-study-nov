"""LLM access layer for Groq.

Why this exists
- Groq free tier allows ~8K tokens/minute *per model*. A city run makes dozens of calls,
  so every call goes through a per-model sliding-window TokenBudget that blocks until
  the estimated tokens fit. The Groq SDK additionally honours `retry-after` on 429.
- Work is routed by *role* so volume tasks (claim extraction) use the small model and
  judgement tasks (planning, verification, answering) use the large one. The fact
  checker therefore never runs on the same model instance as the extractor.
- All calls are structured (strict JSON schema) and return token usage, which is
  accumulated per role so the UI can show the cost of a run.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Literal, TypeVar

from langchain_groq import ChatGroq
from pydantic import BaseModel

from .config import settings

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


def model_for(role: Role) -> str:
    return {
        "planner": settings.groq_model_planner,
        "extractor": settings.groq_model_extractor,
        "checker": settings.groq_model_checker,
        "answer": settings.groq_model_answer,
    }[role]


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
        est = min(est, self.tpm)  # a single oversized call must still be allowed through eventually
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
_usage: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
_usage_lock = threading.Lock()
_chat_cache: dict[tuple, ChatGroq] = {}


def usage_snapshot() -> dict[str, dict[str, int]]:
    with _usage_lock:
        return {k: dict(v) for k, v in _usage.items()}


def get_chat(role: Role, *, temperature: float = 0.0, max_tokens: int = 1200,
             reasoning_effort: str = "low") -> ChatGroq:
    key = (role, temperature, max_tokens, reasoning_effort)
    if key not in _chat_cache:
        _chat_cache[key] = ChatGroq(
            model=model_for(role), api_key=settings.groq_api_key, temperature=temperature,
            max_tokens=max_tokens, reasoning_effort=reasoning_effort,
            max_retries=settings.groq_max_retries, timeout=90,
        )
    return _chat_cache[key]


def structured_call(role: Role, schema: type[T], system: str, user: str, *,
                    max_tokens: int = 1200, reasoning_effort: str = "low",
                    temperature: float = 0.0, strict: bool = True) -> tuple[T, dict]:
    """One structured LLM call under the token budget. Returns (parsed, usage)."""
    model = model_for(role)
    est = estimate_tokens(system) + estimate_tokens(user) + max_tokens
    ticket = _budget.acquire(model, est)
    chat = get_chat(role, temperature=temperature, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
    runnable = chat.with_structured_output(schema, method="json_schema", strict=strict, include_raw=True)
    t0 = time.time()
    try:
        out = runnable.invoke([("system", system), ("user", user)])
    except Exception:
        _budget.settle(ticket, est)
        raise
    raw = out["raw"]
    usage = (getattr(raw, "response_metadata", {}) or {}).get("token_usage") or {}
    total = usage.get("total_tokens")
    _budget.settle(ticket, total)
    with _usage_lock:
        u = _usage[role]
        u["calls"] += 1
        u["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
        u["completion_tokens"] += usage.get("completion_tokens", 0) or 0
    log.info("llm %s/%s %.1fs tokens=%s", role, model, time.time() - t0, total)
    if out.get("parsed") is None:
        raise LLMParseError(f"{role}: {out.get('parsing_error')}")
    return out["parsed"], usage
