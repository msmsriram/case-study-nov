"""Deterministic geography guard.

The models label the geographic level of each claim, and they get one case wrong often enough to matter:
the *administrative region that contains the city* ("Greater Accra Region", "Maharashtra", "Dakar Region")
is treated as if it were the city. The planner already resolves city, aliases and admin region separately,
so this is checkable in code: a statement that names the region but never the city itself cannot be
city-level evidence, whatever a model says.
"""
from __future__ import annotations

import re

LOCAL_LEVELS = {"city", "metro", "district"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def region_only(text: str, city: str, aliases: list[str], admin_region: str | None) -> bool:
    """True when `text` names the admin region but not the city on its own."""
    if not admin_region:
        return False
    t, region = _norm(text), _norm(admin_region)
    if not region or region not in t:
        return False
    # "Greater Accra" contains "Accra": remove the region mentions first, then look for the city by itself
    remainder = t.replace(region, " ")
    for extra in ("region", "county", "state", "province", "metropolitan area"):
        remainder = remainder.replace(f"{region} {extra}", " ")
    names = [_norm(n) for n in [city, *aliases] if n]
    # an alias that is just the region under another spelling does not count as naming the city
    names = [n for n in names if n and n != region and region not in n]
    return not any(re.search(rf"\b{re.escape(n)}\b", remainder) for n in names)


def enforce_level(level: str, statement: str, quote: str, *, city: str, aliases: list[str],
                  admin_region: str | None) -> tuple[str, bool]:
    """Return (level, changed). Region-only evidence is capped at 'state' (shown as Regional)."""
    if level in LOCAL_LEVELS and region_only(f"{statement} {quote}", city, aliases, admin_region):
        return "state", True
    return level, False
