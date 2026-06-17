"""Checkpoint database initialization and (lazy) SqliteSaver factory.

Two responsibilities, deliberately separated so the foundation works whether
or not langgraph is installed yet:

  1. init_checkpoint_db()  - creates/validates the checkpoint sqlite file
                             using ONLY stdlib sqlite3. Always available.
  2. get_checkpointer()    - returns a langgraph SqliteSaver. Imported lazily;
                             raises a clear error if langgraph is absent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.logging_config import get_logger
from graph.config import CHECKPOINT_DB_PATH

logger = get_logger(__name__)


def init_checkpoint_db(path: Path | str | None = None) -> Path:
    """Create the checkpoint DB file (and parent dir) and verify it is a
    usable sqlite database. Returns the resolved path. Stdlib only."""
    db_path = Path(path) if path is not None else CHECKPOINT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Touch + integrity-check so "initializes" is a real, verified outcome.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        ok = conn.execute("PRAGMA integrity_check;").fetchone()
        if not ok or ok[0] != "ok":
            raise RuntimeError(f"checkpoint DB integrity check failed: {ok}")
    finally:
        conn.close()
    logger.info("Checkpoint DB initialized at %s", db_path)
    return db_path


def langgraph_available() -> bool:
    """True if the langgraph sqlite checkpoint backend is importable."""
    try:
        import langgraph.checkpoint.sqlite  # noqa: F401
        return True
    except Exception:
        return False


def get_checkpointer(path: Path | str | None = None):
    """Return a persistent langgraph SqliteSaver bound to the checkpoint DB.

    Uses a long-lived sqlite connection (NOT the from_conn_string context
    manager, which closes on exit) so the same saver can serve many graph
    invocations across calls. Lazily imports langgraph so the foundation
    imports cleanly without it installed; raises a clear error if missing.
    """
    db_path = init_checkpoint_db(path)
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise ImportError(
            "langgraph checkpoint backend not installed. Add "
            "'langgraph-checkpoint-sqlite' to requirements and install it."
        ) from exc
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn, serde=_career_serde())
    saver.setup()
    return saver


def _career_serde():
    """Build a serializer that explicitly allows our first-party schema types.

    Avoids langgraph's 'unregistered type' deprecation warnings and the future
    hard-block, without resorting to allow-all. The list is derived from the
    schemas package so it needs no manual maintenance.
    """
    from enum import Enum

    from pydantic import BaseModel
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    import schemas

    allowed = set()
    for name in dir(schemas):
        obj = getattr(schemas, name)
        if isinstance(obj, type) and issubclass(obj, (BaseModel, Enum)):
            allowed.add((obj.__module__, obj.__qualname__))
    allowed_list = list(allowed)
    return JsonPlusSerializer(
        allowed_msgpack_modules=allowed_list,
        allowed_json_modules=allowed_list,
    )
