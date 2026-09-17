"""API key pools with rotation.

Keys are declared sequentially in .env (SERPER_API_KEY_1, _2, ... / OLLAMA_API_KEY_1, _2, ...).
A KeyRing hands out keys round-robin, limits concurrency per key, and retires a key for a
cool-down period when the provider says it is exhausted, unauthorised or rate limited.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager

from dotenv import dotenv_values

from .config import BACKEND_DIR

log = logging.getLogger(__name__)
_file_env = dotenv_values(BACKEND_DIR / ".env")


def numbered_env(prefix: str, limit: int = 50) -> list[str]:
    """PREFIX_1, PREFIX_2, ... from the process env or backend/.env; also accepts bare PREFIX."""
    out: list[str] = []
    for name in [prefix] + [f"{prefix}_{i}" for i in range(1, limit + 1)]:
        v = (os.environ.get(name) or _file_env.get(name) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


class NoKeyAvailable(RuntimeError):
    pass


class KeyRing:
    def __init__(self, name: str, keys: list[str], per_key_concurrency: int = 1):
        self.name = name
        self._keys = list(keys)
        self._sem = {k: threading.BoundedSemaphore(per_key_concurrency) for k in self._keys}
        self._retired_until: dict[str, float] = {}
        self._next = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    def _alive(self) -> list[str]:
        now = time.time()
        return [k for k in self._keys if self._retired_until.get(k, 0) <= now]

    def retire(self, key: str, seconds: float, why: str) -> None:
        with self._lock:
            self._retired_until[key] = time.time() + seconds
        log.warning("%s key ...%s retired for %.0fs: %s (%d/%d alive)",
                    self.name, key[-4:], seconds, why, len(self._alive()), len(self._keys))

    @contextmanager
    def lease(self, wait_seconds: float = 120.0):
        """Borrow a key. Blocks while all live keys are busy; raises if none are alive."""
        deadline = time.time() + wait_seconds
        while True:
            alive = self._alive()
            if not alive:
                raise NoKeyAvailable(f"{self.name}: all {len(self._keys)} keys are exhausted or retired")
            with self._lock:
                order = alive[self._next % len(alive):] + alive[: self._next % len(alive)]
                self._next += 1
            for k in order:
                if self._sem[k].acquire(blocking=False):
                    try:
                        yield k
                    finally:
                        self._sem[k].release()
                    return
            if time.time() > deadline:
                raise NoKeyAvailable(f"{self.name}: timed out waiting for a free key")
            time.sleep(0.05)
