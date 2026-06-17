"""LangGraph-foundation configuration.

Reuses core.config.BASE_DIR so paths stay consistent with the existing
project. Reads overridable values from the environment (.env only — never
hard-coded secrets).
"""
from __future__ import annotations

import os
from pathlib import Path

from core.config import BASE_DIR

# --- Checkpoint (LangGraph control-plane state) ---
CHECKPOINT_DB_PATH: Path = Path(
    os.getenv("CHECKPOINT_DB_PATH", str(BASE_DIR / "data" / "langgraph_checkpoints.db"))
)

# --- Domain DB (business data) — mirrors core.config.DB_PATH ---
CAREER_DB_PATH: Path = Path(
    os.getenv("CAREER_DB_PATH", str(BASE_DIR / "data" / "career_agent.db"))
)

# --- Orchestration ---
MAX_NODE_RETRIES: int = int(os.getenv("MAX_NODE_RETRIES", "2"))
DEFAULT_THREAD_PREFIX: str = os.getenv("DEFAULT_THREAD_PREFIX", "career-run")
