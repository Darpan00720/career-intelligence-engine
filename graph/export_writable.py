"""Export terminal stage (v6) — pure projection of the DB to jobs_master.xlsx.

No new replay model: export is a deterministic projection that OVERWRITES a fixed
path (outputs/jobs_master.xlsx — the single source of truth) every run, so it is
idempotent by construction. Re-running after a crash simply rewrites the same
file from current DB state.
"""
from __future__ import annotations

from core.logging_config import get_logger

logger = get_logger(__name__)


def run_export_stage(state: dict | None = None) -> dict:
    """Project the ranked dashboard to xlsx. Returns {path}. Idempotent overwrite."""
    from core.exporter import export_jobs_master_xlsx

    path = export_jobs_master_xlsx()
    logger.info("export: wrote %s", path)
    return {"path": str(path)}
