"""Career service: thin orchestration over the compiled LangGraph.

No business/scoring/recommendation/output logic — only: build & cache the
compiled graph + checkpointer, run/resume it, read state, and report health.
The deterministic engine and serializers are invoked, never reimplemented.
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache

from core.logging_config import get_logger
from core.observability import timed
from core.settings import settings
from graph.build import build_graph
from graph.checkpoint import get_checkpointer
from graph.providers import DbJobProvider, clear_provider, set_provider
from graph.state import new_career_state

logger = get_logger("career_service")


@lru_cache(maxsize=1)
def _graph():
    """Compile the graph once with a persistent SqliteSaver (process singleton)."""
    return build_graph(checkpointer=get_checkpointer(settings.checkpoint_path))


def warmup() -> None:
    """Compile the graph eagerly (called at API startup)."""
    _graph()


def analyze_profile(thread_id: str, profile_path: str | None = None,
                    limit: int | None = None) -> dict:
    """Run the full pipeline for a profile under thread_id. Returns final state."""
    profile_path = profile_path or "candidate_profile.json"
    set_provider(thread_id, DbJobProvider(limit=limit or settings.db_job_limit))
    try:
        cfg = {"configurable": {"thread_id": thread_id}}
        with timed("analyze", thread_id=thread_id):
            state = _graph().invoke(
                new_career_state(run_id=thread_id, profile_path=profile_path), cfg)
        # Observability: snapshot this run's per-stage stats (read-only, additive).
        try:
            from core import pipeline_metrics
            pipeline_metrics.record_run(state)
        except Exception:  # metrics capture must never affect the run result
            logger.exception("pipeline_metrics.record_run failed")
        return state
    finally:
        clear_provider(thread_id)


def resume_run(thread_id: str) -> dict:
    """Resume a thread from its last checkpoint (no new input)."""
    set_provider(thread_id, DbJobProvider(limit=settings.db_job_limit))
    try:
        cfg = {"configurable": {"thread_id": thread_id}}
        with timed("resume", thread_id=thread_id):
            return _graph().invoke(None, cfg)
    finally:
        clear_provider(thread_id)


def get_run(thread_id: str) -> dict | None:
    """Return the persisted state snapshot for a thread, or None if absent."""
    cfg = {"configurable": {"thread_id": thread_id}}
    snap = _graph().get_state(cfg)
    return snap.values if snap and snap.values else None


def health_check() -> dict:
    return {"status": "healthy"}


def ready_check() -> dict:
    """Verify external dependencies are reachable."""
    database = _check_sqlite(settings.database_url)
    checkpoint_storage = _check_sqlite(settings.checkpoint_path, allow_create=True)
    graph_compiled = _check_graph()
    embeddings_loaded = _check_embeddings()
    status = "ready" if all(
        [database, checkpoint_storage, graph_compiled, embeddings_loaded]) else "not_ready"
    return {
        "status": status,
        "database": database,
        "checkpoint_storage": checkpoint_storage,
        "graph_compiled": graph_compiled,
        "embeddings_loaded": embeddings_loaded,
    }


def _check_sqlite(path: str, allow_create: bool = False) -> bool:
    conn = None
    try:
        mode = "" if allow_create else "?mode=ro"
        conn = sqlite3.connect(f"file:{path}{mode}", uri=True)
        conn.execute("PRAGMA schema_version;")
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _check_graph() -> bool:
    try:
        return _graph() is not None
    except Exception:
        return False


def _check_embeddings() -> bool:
    """Truthfully report embedding-model availability (R4).

    Returns True only if the model is already loaded OR can be loaded
    successfully. No scoring, no state mutation. `warm_embedding_model()`
    loads the model once (cached); subsequent calls are cheap.
    """
    try:
        from core import semantic_matcher
        if getattr(semantic_matcher, "_model", None) is not None:
            return True
        return bool(semantic_matcher.warm_embedding_model())
    except Exception:
        return False


def shutdown() -> None:
    """Close the singleton checkpointer connection and reset the graph cache.

    Called at API shutdown so no SQLite connection is leaked (R3)."""
    try:
        graph = _graph()
        conn = getattr(getattr(graph, "checkpointer", None), "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    finally:
        _graph.cache_clear()
