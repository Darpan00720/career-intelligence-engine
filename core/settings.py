"""Immutable, environment-driven runtime configuration.

Plain (no pydantic-settings dependency). Values are read once at import from
the environment with safe defaults, then frozen. Startup is deterministic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.config import BASE_DIR
from core.version import VERSION


@dataclass(frozen=True)
class Settings:
    database_url: str
    checkpoint_path: str
    log_level: str
    model_provider: str
    embedding_model: str
    api_version: str
    environment: str
    host: str
    port: int
    db_job_limit: int

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            database_url=os.getenv("DATABASE_URL", str(BASE_DIR / "data" / "career_agent.db")),
            checkpoint_path=os.getenv(
                "CHECKPOINT_PATH", str(BASE_DIR / "data" / "langgraph_checkpoints.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            model_provider=os.getenv("MODEL_PROVIDER", "anthropic"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            api_version=os.getenv("API_VERSION", VERSION),
            environment=os.getenv("ENVIRONMENT", "development"),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            db_job_limit=int(os.getenv("DB_JOB_LIMIT", "10")),
        )


# Frozen, process-wide singleton.
settings = Settings.from_env()
