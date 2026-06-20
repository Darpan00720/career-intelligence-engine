"""Terminal-stage runner (v6) — ordered, gated post-analysis side-effect stages.

After the analytical chain, a single ``terminal`` node executes whichever
terminal stages are enabled, in canonical order: ``documents -> export ->
tracker``. This deliberately avoids linear phase-chaining (where each stage gates
off the previous and breaks if an intermediate is disabled): the runner is ONE
node + ONE ``Phase.TERMINAL`` + ONE routing gate, and the stages stay independent
and feature-gate-orthogonal.

  RECOMMENDATIONS
       |
   terminal runner ──> [documents] [export] [tracker]   (only the enabled ones,
       |                                                  in order)
   output_experience

Replay: the runner re-executes as a unit on resume; safety comes entirely from
each stage being individually idempotent (documents = DB-authoritative content +
write-if-missing projection; export = deterministic overwrite projection; tracker
= insert-if-missing). No new replay model is introduced here — only routing
ergonomics.
"""
from __future__ import annotations

from core.logging_config import get_logger
from schemas.control import Phase

logger = get_logger(__name__)


def _stages() -> list[tuple[str, callable, callable]]:
    """(name, enabled_predicate, run_fn) in canonical execution order."""
    from graph.documents_writable import run_documents_stage
    from graph.export_writable import run_export_stage
    from graph.persistence import documents_enabled, export_enabled, tracker_enabled
    from graph.tracker_writable import run_tracker_stage
    return [
        ("documents", documents_enabled, run_documents_stage),
        ("export",    export_enabled,    run_export_stage),
        ("tracker",   tracker_enabled,   run_tracker_stage),
    ]


def any_terminal_enabled() -> bool:
    return any(enabled() for _name, enabled, _fn in _stages())


def terminal_node(state: dict) -> dict:
    """Run each enabled terminal stage in order; isolate per-stage failure.

    Returns ``phase=Phase.TERMINAL`` + ``terminal_stats`` (per-stage results) +
    ``audit_log``. One node so the planner/router treat the terminal block as a
    single unit; stage independence is preserved inside.
    """
    from graph.nodes import _enter

    _enter("terminal")
    results: dict[str, dict] = {}
    for name, enabled, run in _stages():
        if not enabled():
            continue
        try:
            results[name] = run(state)
        except Exception as exc:  # one stage must not abort the others
            logger.exception("terminal stage %s failed", name)
            results[name] = {"error": str(exc)}
    logger.info("terminal: ran %s", list(results))
    return {"phase": Phase.TERMINAL, "terminal_stats": results, "audit_log": ["terminal"]}
