"""Per-domain event logging (v3).

Writes structured, append-only event lines to separate files under the logs
directory (config.LOGS_DIR, which is on the Docker-persisted data volume):

    pipeline.log   research.log   documents.log   tracker.log

Each event line carries: timestamp, level, event, job_id, company, duration,
status, and optional error details. Levels: INFO / WARNING / ERROR.

    from core.event_log import log_event
    log_event("research", "company_researched", job_id=12, company="Acme",
              duration=1.4, status="ok")
"""
from __future__ import annotations

import logging
from pathlib import Path

from core import config

_LOG_NAMES = {"pipeline", "research", "documents", "tracker"}
_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
_loggers: dict[str, logging.Logger] = {}


def get_event_logger(name: str) -> logging.Logger:
    """Return (and cache) a file-backed logger for a domain (e.g. 'pipeline')."""
    if name in _loggers:
        return _loggers[name]

    Path(config.LOGS_DIR).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"event.{name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # keep event files separate from the root logger

    log_path = Path(config.LOGS_DIR) / f"{name}.log"
    # Avoid stacking duplicate handlers on repeated calls / re-imports.
    if not any(isinstance(h, logging.FileHandler)
               and getattr(h, "baseFilename", None) == str(log_path.resolve())
               for h in logger.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)

    _loggers[name] = logger
    return logger


def log_event(name: str, event: str, *, job_id=None, company=None,
              duration: float | None = None, status: str | None = None,
              error: str | None = None, level: str = "INFO") -> None:
    """Emit one structured event line to logs/<name>.log."""
    logger = get_event_logger(name if name in _LOG_NAMES else "pipeline")
    parts = [f"event={event}"]
    if job_id is not None:
        parts.append(f"job_id={job_id}")
    if company:
        parts.append(f"company={company!r}")
    if duration is not None:
        parts.append(f"duration={duration:.2f}s")
    if status:
        parts.append(f"status={status}")
    if error:
        parts.append(f"error={error!r}")
    logger.log(getattr(logging, level.upper(), logging.INFO), " | ".join(parts))
