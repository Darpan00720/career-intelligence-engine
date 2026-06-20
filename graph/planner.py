"""Planner Agent (v6 Phase 2) — deterministic, rule-based plan generation.

Turns a free-text ``user_query`` into a validated :class:`ExecutionPlan`. Phase 2
uses **deterministic keyword rules only** (no LLM): an intent is mapped to a
*terminal* agent, and the plan is that agent plus all of its transitive
prerequisites, emitted in canonical pipeline order. The plan is validated as a
DAG via the existing ``core.workflow_dag.DependencyGraph`` and falls back to the
full pipeline on any ambiguity or validation failure.

This module does NOT route or execute. ``planner_node`` only writes the plan
into state; the supervisor's phase-based routing is untouched (Phase 3 wires the
dynamic router). An LLM intent parser can later replace ``classify_intent``
behind the same signature.
"""
from __future__ import annotations

import uuid

from core.logging_config import get_logger
from core.workflow_dag import DependencyGraph, ExecutionPlanner, WorkflowNode
from graph.state import CareerState
from schemas.execution import ExecutionPlan, Task

logger = get_logger(__name__)

# Canonical pipeline: node name -> its direct prerequisites. This is the single
# source of truth for agent dependencies (mirrors the legacy linear chain).
CANONICAL_DEPS: dict[str, list[str]] = {
    "profile_strategy": [],
    "job_ingestion": ["profile_strategy"],
    "taxonomy": ["job_ingestion"],
    "scoring": ["taxonomy"],
    "opportunity_intel": ["scoring"],
    "prioritization": ["opportunity_intel"],
    "recommendations": ["prioritization"],
    "output_experience": ["recommendations"],
}

# Deterministic execution order (also the order tasks are emitted in a plan).
CANONICAL_ORDER: list[str] = list(CANONICAL_DEPS)

# Human-readable capability label per agent (for explainability / registry lookup).
CAPABILITY: dict[str, str] = {
    "profile_strategy": "profile",
    "acquire_jobs": "acquire_jobs",
    "research": "research",
    "job_ingestion": "ingest_jobs",
    "taxonomy": "classify_jobs",
    "scoring": "score_jobs",
    "opportunity_intel": "opportunity_intelligence",
    "prioritization": "prioritize",
    "recommendations": "recommend",
    "terminal": "terminal_stages",
    "output_experience": "present_output",
}

# Intent rules: the FIRST rule whose any-keyword matches wins. Each maps to a
# terminal agent; the plan includes that agent + all transitive prerequisites.
# Ordered most-specific (shortest pipeline) first so e.g. "ingest" doesn't get
# swallowed by a broader match. The default (no match) is the full pipeline.
_INTENT_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("ingest", "fetch jobs", "find jobs", "collect jobs", "scrape"),
     "job_ingestion", "ingest_only"),
    (("classify", "taxonomy", "categorize", "categorise"),
     "taxonomy", "classify_only"),
    (("score",
  "scoring",
  "rescore",
  "re-score",
  "re score",
  "just score",
  "score only"),
 "scoring",
 "rescore"),
    (("opportunity", "intel", "intelligence"),
     "opportunity_intel", "intelligence_only"),
    (("prioritize", "prioritise", "rank", "ranking", "shortlist"),
     "prioritization", "prioritize"),
    (("recommend", "recommendation", "suggest", "what should i apply"),
     "recommendations", "recommend"),
    (("report", "dashboard", "export", "summary", "output", "present"),
     "output_experience", "full_output"),
]

_DEFAULT_TARGET = "output_experience"
_DEFAULT_KIND = "full_pipeline"


# Opt-in write-capable stages, each inserted between a predecessor and the
# successor whose dependency it takes over. (node, predecessor, successor, flag).
# Adding the next stage (documents/export) is a one-line append here.
def _optional_stages() -> list[tuple[str, str, str, bool]]:
    from graph.persistence import acquisition_enabled, research_enabled
    from graph.terminal_stages import any_terminal_enabled
    return [
        ("acquire_jobs", "profile_strategy", "job_ingestion",    acquisition_enabled()),
        ("research",     "scoring",          "opportunity_intel", research_enabled()),
        # The terminal runner (documents/export/tracker) is one planner node.
        ("terminal",     "recommendations",  "output_experience", any_terminal_enabled()),
    ]


def _active_deps() -> dict[str, list[str]]:
    """Canonical deps, augmented with whichever opt-in stages are enabled.

    Each enabled stage is spliced in (``successor`` now depends on it). Default
    (all flags off) is the base 8-node chain unchanged.
    """
    deps = {k: list(v) for k, v in CANONICAL_DEPS.items()}
    for node, pred, succ, enabled in _optional_stages():
        if enabled:
            deps[node] = [pred]
            deps[succ] = [node]
    return deps


def _active_order() -> list[str]:
    order = list(CANONICAL_ORDER)
    for node, _pred, succ, enabled in _optional_stages():
        if enabled and node not in order:
            order.insert(order.index(succ), node)
    return order


def classify_intent(user_query: str) -> tuple[str, str]:
    """Map a query to a (terminal_agent, intent_kind) pair. Deterministic.

    Returns the full-pipeline default when the query is empty or unmatched.
    """
    q = (user_query or "").strip().lower()
    if not q:
        return _DEFAULT_TARGET, _DEFAULT_KIND
    for keywords, target, kind in _INTENT_RULES:
        if any(kw in q for kw in keywords):
            return target, kind
    return _DEFAULT_TARGET, _DEFAULT_KIND


def select_agents(target: str) -> list[str]:
    """Return ``target`` plus all transitive prerequisites, in canonical order."""
    deps = _active_deps()
    order = _active_order()
    if target not in deps:
        target = _DEFAULT_TARGET
    needed: set[str] = set()

    def _collect(agent: str) -> None:
        if agent in needed:
            return
        needed.add(agent)
        for dep in deps[agent]:
            _collect(dep)

    _collect(target)
    return [a for a in order if a in needed]


def _make_tasks(agents: list[str]) -> list[Task]:
    deps = _active_deps()
    included = set(agents)
    return [
        Task(
            id=agent,
            agent=agent,
            capability=CAPABILITY.get(agent, ""),
            # only keep deps that are part of this plan (always true for a
            # prefix-closed selection, but filtered defensively)
            depends_on=[d for d in deps[agent] if d in included],
        )
        for agent in agents
    ]


def _full_pipeline_tasks() -> list[Task]:
    return _make_tasks(_active_order())


def build_dependency_graph(plan: ExecutionPlan) -> DependencyGraph:
    """Build a validated core.workflow_dag.DependencyGraph from a plan.

    The ``WorkflowNode.func`` is a no-op identity — only the name/depends_on are
    used (validation + levelization). Raises ValueError/CycleError on a bad DAG.
    """
    return DependencyGraph([
        WorkflowNode(name=t.id, func=lambda ctx: ctx, depends_on=list(t.depends_on))
        for t in plan.tasks
    ])


def plan_levels(plan: ExecutionPlan) -> list[list[str]]:
    """Parallel execution levels for a plan, via ExecutionPlanner.

    Each inner list is a set of mutually-independent tasks that may run in
    parallel; levels run in order. For the linear canonical pipeline this is one
    task per level; for a branching plan, independent tasks share a level.
    """
    return ExecutionPlanner.plan(build_dependency_graph(plan))


def build_execution_plan(user_query: str, *, plan_id: str | None = None) -> ExecutionPlan:
    """Deterministically build a validated ExecutionPlan from a user query.

    The plan is validated and levelized via DependencyGraph + ExecutionPlanner
    (deps exist, acyclic). Any failure falls back to the full canonical pipeline
    so a plan is always valid and runnable.
    """
    target, kind = classify_intent(user_query)
    plan = ExecutionPlan(
        plan_id=plan_id or uuid.uuid4().hex,
        user_query=user_query or "",
        intent_kind=kind,
        tasks=_make_tasks(select_agents(target)),
    )

    try:
        levels = plan_levels(plan)  # validates the DAG and groups parallel tasks
        logger.info("planner: %d task(s) across %d level(s): %s",
                    len(plan.tasks), len(levels), levels)
    except Exception as exc:  # noqa: BLE001 - bad/ambiguous plan -> safe fallback
        logger.warning("planner: invalid plan for query %r (%s); using full pipeline",
                       user_query, exc)
        plan = ExecutionPlan(
            plan_id=plan_id or uuid.uuid4().hex,
            user_query=user_query or "",
            intent_kind=_DEFAULT_KIND,
            tasks=_full_pipeline_tasks(),
        )

    return plan


def planner_node(state: CareerState) -> dict:
    """Graph node: read ``user_query`` from state, produce + store an ExecutionPlan.

    Returns a partial state update only. Does not set ``phase`` or otherwise
    influence the (still phase-based) supervisor routing.
    """
    logger.info("node: planner")
    query = state.get("user_query") or ""
    plan = build_execution_plan(query)
    return {
        "execution_plan": plan,
        "pending_tasks": plan.task_ids(),
        "audit_log": ["planner"],
    }
