# Graph Convergence — Migration Framework (ADR)

## Decision Record — v7.0.0 "LangGraph Coexistence Release"

**Decision:** Adopt coexistence between the legacy pipeline and the LangGraph
execution engine.

**Status:** Accepted.

**Consequences:**
- Legacy `run_autonomous_pipeline` remains the **authoritative** system of record.
- The graph is a **replay-safe, effect-capable, opt-in** execution engine.
- All write-capable stages are gated by flags that **default off**.
- Shared implementations (graph stages wrap legacy helpers) keep maintenance cost low.
- **Replacement** (graph as system of record) remains a future architectural
  option, **intentionally deferred** until it becomes a business requirement.
- **Production-hardening** work is deferred with it (see Backlog).

### Backlog

**Deferred** (activate only if/when Replace becomes a requirement):
- Multi-worker support · distributed checkpointing · gateway-routed resilience
  (circuit breaker/retry for research+documents) · load characterization ·
  production telemetry · the Replace cutover itself.

**Out of scope** (no remaining need):
- Additional migration work · further capability convergence.

---

The detail below records how the framework was proven (kept for engineering
reference). Framework status: **PROVEN** across the four side-effect classes
(acquisition, scoring, research, documents) with end-to-end replay evidence.

## Context

The LangGraph graph began as a pure, read-only analysis engine; the legacy
`run_autonomous_pipeline` owned all side effects (acquire, score-persist,
research, documents, export, tracker). We are progressively folding those
side-effecting stages into the graph so it can replace the legacy pipeline.

## Decision — canonical rule (Option A)

> **A new *capability* becomes a new node + a new `Phase`. A *side effect of an
> existing capability* is folded into that capability's node.**

Corollaries:
- Acquisition is a new stage → its own node (`acquire_jobs`) + `Phase.ACQUISITION`.
- Score persistence is a side effect of scoring → folded into `scoring_node`.
- "Projection nodes must stay pure" is **explicitly retired** as a universal rule.
  Separation is preserved at the *stage* level, not within every node.

## Every write node MUST be idempotent — mechanism chosen per side-effect class

| Side-effect class | Idempotency mechanism | Node |
|---|---|---|
| New data acquired (uniqueness) | content `dedup_hash` + UNIQUE `url`; plus a run-level `ingestion_runs` marker | `acquire_jobs` |
| Recomputable record | upsert `ON CONFLICT(job_id)` | `scoring` (persist) |
| Expensive external call | TTL staleness cache (`research_is_stale`) — don't repeat fresh work | `research` ✅ |
| Expensive external call producing an artifact | content-hash + version (DB-authoritative `content`); file = write-only-if-missing projection | `documents` ✅ |

The *guarantee* (idempotent under replay/retry) is uniform; the *mechanism*
matches the cost/semantics of the write.

### Authoritative artifacts (generalized principle)

- Expensive, externally-generated artifacts are persisted in **authoritative
  storage** (the DB), keyed by a content hash (+ version).
- Filesystem outputs are **projections**: they must be reconstructable from
  authoritative storage **without re-executing the external side effect**.
- Projection materialization is **idempotent and write-only-if-missing** (never
  clobbers a user's edits; a genuine content change produces a *new* version).

This is the **artifact-generation pattern** (first applied by `documents`): the
paid generation is content-hash-gated and DB-persisted; the file is a free,
idempotent projection of the DB.

## Feature gating

All write-capable stages are opt-in via env flags (default OFF), so the default
graph is read-only and the test suite is unchanged:
- `ENABLE_ACQUISITION` → run `acquire_jobs` before `job_ingestion`
- `PERSIST_SCORES` → `scoring` upserts to the scores table
- `ENABLE_RESEARCH` (planned) → run `research` after `scoring`

## Evidence

- Node-level idempotency tests (`tests/test_writable_ingestion.py`,
  `tests/test_acquisition_wiring.py`).
- **End-to-end replay** through the real compiled graph + SqliteSaver
  (`tests/test_replay_idempotency.py`): interrupt→resume yields no duplicate jobs
  and scores persisted exactly once; two independent runs over identical data
  show no duplication. (Building this caught a real observability bug — the
  acquisition node was not registering in `EXECUTION_LOG`.)

## Migration checklist (apply per remaining stage)

1. `Phase.<NAME>` in `schemas/control.py` (only if it's a new capability).
2. Gated flag in `graph/persistence.py`.
3. Write node wrapping the existing `core`/`agents` module; choose the
   idempotency mechanism for its side-effect class.
4. `graph/routing.py`: gate the phase transition (data-driven table once >3 gates).
5. `graph/build.py`: register the node (orphan-safe target).
6. `graph/planner.py`: extend `_active_deps()`/`_active_order()`.
7. Tests: wiring + an idempotency/replay test for the new mechanism.

## Progress

- ✅ acquisition (`acquire_jobs`, `Phase.ACQUISITION`, `ENABLE_ACQUISITION`)
- ✅ score persistence (folded into `scoring_node`, `PERSIST_SCORES`)
- ✅ research (`research`, `Phase.RESEARCH`, `ENABLE_RESEARCH`; TTL idempotency)
- ✅ documents (`Phase.DOCUMENTS`, `ENABLE_DOCUMENTS`; DB-authoritative content +
  write-only-if-missing file projection — first artifact-generation stage)
- ✅ export (`ENABLE_EXPORT`; pure projection — idempotent overwrite of jobs_master.xlsx)
- ✅ tracker (`ENABLE_TRACKER`; persistence only — insert-if-missing application rows)

Phase routing uses a **gated-transition table** (`graph.routing._gated_transitions`)
and the planner a parallel `_optional_stages()` list, so each new stage is a
one-line addition to both.

### Terminal-stage runner

`documents`, `export`, and `tracker` are post-analysis terminal stages. Rather
than linear chaining (each gating off the previous — fragile if an intermediate
is disabled), they run inside a single `terminal` node (`Phase.TERMINAL`,
`graph/terminal_stages.py`) that executes whichever are enabled, in canonical
order (documents → export → tracker), behind ONE routing gate. Stages stay
independent and feature-gate-orthogonal. Replay safety is inherited from each
stage's own idempotency; the runner re-executes as a unit on resume.

## Status — convergence COMPLETE

All six legacy side-effecting stages are migrated, gated, and replay-tested. The
LangGraph graph can now (with flags on) perform the full pipeline the legacy
`run_autonomous_pipeline` owned; the legacy entry point remains for CLI use.

## Limitations / NOT yet evidenced (explicit)

What is proven is **logic-level replay safety + wiring** (tests, including
interrupt/resume via a real SqliteSaver). The following are NOT yet evidenced:

1. **Production deployment** — no rollout, telemetry, or load characteristics.
2. **Migration intent** — DECIDED: coexistence adopted, Replace deferred (see
   the Decision section above). Revisit Replace only on a business trigger.
3. **Production failure behavior** — process crashes, multi-worker, long-running
   workloads, and real LLM/API failures are not observed.

### DECISION (recorded): Coexistence; Replace deferred

**Adopted operating model: Coexistence** — flags default-off, legacy
authoritative, graph opt-in. This is the repository's current state; no further
code was needed to adopt it. Lowest risk, lowest maintenance (the graph wraps the
legacy helpers, so there is one implementation of each side effect, two entry
points).

**Replace (graph as system of record) is deferred** — to be revisited only when
there is a business reason to incur the Phase-2 production-hardening cost. At that
point the remaining ⚠️ items below become a hardening program, not features.

**Correctness fix landed (direction-independent):** document file writes are now
**atomic** (`agents.document_agent.atomic_write_text` — temp-file + fsync +
`os.replace`), used by `_write_doc` (both entry points) and the graph's
write-only-if-missing projection. A crash mid-write can no longer leave a
truncated file at the canonical path, so "file exists" ⟺ "file is complete".
Evidenced by `tests/test_documents_wiring.py::...test_atomic_write_leaves_no_truncated_file`.
Residual (deliberate, not a bug): an *externally* corrupted existing file is not
re-checked, because write-only-if-missing preserves user edits — the atomicity
-vs-never-clobber tradeoff is resolved in favour of edit preservation.

Known code-level fragilities behind (3):

- `interrupt_before` (cooperative pause) ≠ a real mid-write crash (still unevidenced).
- ~~File writes are non-atomic~~ — **FIXED**: document writes are atomic
  (`atomic_write_text`); the truncation defect is closed.
- `research`/`documents` call Anthropic **directly**, bypassing `LLMGateway`
  (no circuit breaker / retry / backoff / fallback). Resilience is currently
  "eventual via re-run," not "robust within a run."
- Provider/raw-source registries, the v2 rate limiter, and the SqliteSaver are
  **process-local**; multi-worker needs a shared (Postgres) checkpointer and
  externalized registries. Dockerfile is `--workers 1` by design.
