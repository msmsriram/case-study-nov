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

    # auto = chain of every configured provider: serper -> tavily -> ollama -> ddgs
    search_provider: Literal["auto", "serper", "tavily", "ollama", "ddgs"] = "auto"
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
    # Re-use search results / fetched pages younger than this. 0 = always live (production).
    research_cache_ttl_hours: float = 0

    # --- LLM ----------------------------------------------------------------
    # Primary provider; the other becomes the automatic fallback when its key(s) exist.
    llm_provider: Literal["ollama", "groq"] = "ollama"
    ollama_base_url: str = "https://ollama.com"
    ollama_model_planner: str = "gpt-oss:120b"
    ollama_model_extractor: str = "gpt-oss:20b"
    ollama_model_checker: str = "gpt-oss:120b"
    ollama_model_answer: str = "gpt-oss:120b"
    ollama_per_key_concurrency: int = 1      # free tier: 1 concurrent request per account

    # --- Groq (fallback) ---------------------------------------------------
    groq_api_key: str | None = None
    # Free tier: 8K tokens/min *per model*, so volume work goes to the small model
    # and judgement work (planning, verification, answering) to the large one.
    groq_model_planner: str = "openai/gpt-oss-120b"
    groq_model_extractor: str = "openai/gpt-oss-20b"
    groq_model_checker: str = "openai/gpt-oss-120b"
    groq_model_answer: str = "openai/gpt-oss-120b"
    groq_tpm_budget: int = 7000          # stay under the 8000 TPM ceiling
    groq_max_retries: int = 4

    # --- Research budget per run ---------------------------------------------
    max_queries_per_run: int = 14
    max_results_per_query: int = 6
    max_documents_per_run: int = 24
    max_passage_tokens_per_document: int = 1200
    max_claims_per_document: int = 8
    verify_batch_size: int = 6

    cache_dir: Path = BACKEND_DIR / ".cache"
    data_dir: Path = BACKEND_DIR / "data"


settings = Settings()
settings.cache_dir.mkdir(parents=True, exist_ok=True)
settings.data_dir.mkdir(parents=True, exist_ok=True)
