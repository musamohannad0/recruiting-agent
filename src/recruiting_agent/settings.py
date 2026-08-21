from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # Paths
    data_dir: Path = PROJECT_ROOT / "data"
    config_dir: Path = PROJECT_ROOT / "config"
    profile_dir: Path = PROJECT_ROOT / "profile"
    workspace_dir: Path = PROJECT_ROOT / "workspace"
    database_url: str = ""

    # LLM models
    prefilter_model: str = "claude-haiku-4-5"
    scoring_model: str = "claude-sonnet-5"
    discovery_model: str = "claude-sonnet-5"
    # Used when the primary model is overloaded; blank disables the fallback.
    fallback_model: str = ""

    # LLM call behaviour
    llm_max_attempts: int = 3  # total attempts per call, transient failures only
    max_call_budget_usd: float | None = None  # hard per-call spend ceiling
    scoring_concurrency: int = 6
    prefilter_concurrency: int = 4
    prefilter_batch_size: int = 25

    # Langfuse (tracing skipped when keys unset)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://us.cloud.langfuse.com"

    # Scheduler intervals
    ingest_interval_hours: int = 2
    discovery_interval_days: int = 7

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def effective_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.db_path}"

    @property
    def companies_yaml(self) -> Path:
        return self.config_dir / "companies.yaml"


settings = Settings()
