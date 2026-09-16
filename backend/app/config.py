"""Central settings. Values come from backend/.env (see .env.example)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    search_provider: Literal["auto", "tavily", "ddgs"] = "auto"
    tavily_api_key: str | None = None

    user_agent: str = (
        "Mozilla/5.0 (compatible; CARDIO4CitiesResearchBot/0.1; "
        "+https://www.cardio4cities.org/)"
    )
    min_seconds_between_requests_per_domain: float = 1.0
    request_timeout_seconds: float = 20.0
    max_document_bytes: int = 8_000_000
    max_text_chars: int = 60_000
    max_pdf_pages: int = 40
    robots_unreachable_policy: Literal["deny", "allow"] = "deny"

    cache_dir: Path = BACKEND_DIR / ".cache"


settings = Settings()
settings.cache_dir.mkdir(parents=True, exist_ok=True)
