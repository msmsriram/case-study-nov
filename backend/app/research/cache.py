"""Tiny JSON disk cache for search results and fetched documents.

Development aid and a modest politeness feature (do not re-crawl the same page
within the TTL). TTL 0 disables it, which is the production default so that the
"live research at request time" guarantee holds.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..config import settings


class DiskCache:
    def __init__(self, namespace: str):
        self.dir = settings.cache_dir / namespace
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = settings.research_cache_ttl_hours * 3600

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json")

    def get(self, key: str) -> Any | None:
        if self.ttl <= 0:
            return None
        p = self._path(key)
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if time.time() - payload.get("t", 0) > self.ttl:
            return None
        return payload.get("v")

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        self._path(key).write_text(json.dumps({"t": time.time(), "v": value}), encoding="utf-8")
