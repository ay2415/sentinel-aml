"""Central, typed configuration. Every value can be overridden with an environment variable.

Production secrets (JWT secret, database password, Anthropic key) are injected from
Azure Key Vault into the container environment; they are never committed.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

_DEFAULT_JWT = "local-dev-only-secret-change-me-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    environment: str = "local"  # local | test | production
    app_name: str = "SentinelAML"
    database_url: str = "postgresql+psycopg2://sentinel:sentinel@localhost:5432/sentinel"
    db_pool_size: int = 10

    # --- security ---
    jwt_secret: str = _DEFAULT_JWT
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    rate_limit_per_minute: int = 120
    login_rate_limit_per_minute: int = 10
    max_upload_bytes: int = 20_000_000
    cors_origins: list[str] = ["http://localhost:8501"]

    # --- LLM gateway ---
    llm_provider: str = "deterministic"  # deterministic | anthropic
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    model_reasoning: str = "claude-sonnet-5"
    model_fast: str = "claude-haiku-4-5-20251001"
    llm_timeout_seconds: float = 45.0
    llm_max_retries: int = 2
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: int = 60
    # USD per million tokens. VERIFY against the current Anthropic pricing page before relying on cost figures.
    model_pricing: dict[str, dict[str, float]] = {
        "claude-sonnet-5": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    }

    # --- RAG ---
    embedding_provider: str = "glove"  # glove (local, pretrained word vectors) | bge (production)
    embedding_model: str = "glove-wiki-gigaword-300-sif"
    embedding_dim: int = 300
    embedding_cache_dir: Path = REPO_ROOT / "models" / "embeddings"
    rag_top_k: int = 6
    rag_candidate_k: int = 30

    # --- agents ---
    agent_max_tool_steps: int = 8
    decision_max_retries: int = 1

    # --- paths ---
    models_dir: Path = REPO_ROOT / "models"
    prompts_dir: Path = REPO_ROOT / "prompts"
    knowledge_base_dir: Path = REPO_ROOT / "data" / "knowledge_base"
    data_dir: Path = REPO_ROOT / "data"

    # --- observability ---
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if self.jwt_secret == _DEFAULT_JWT or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be set to a strong secret in production")
            if self.llm_provider == "anthropic" and not self.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
            if "*" in self.cors_origins:
                raise ValueError("Wildcard CORS is not allowed in production")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
