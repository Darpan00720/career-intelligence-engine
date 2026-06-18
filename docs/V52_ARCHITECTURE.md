# Career Intelligence Engine v5.2 — Async Runtime, Flags, Temporal Evaluation

Status: **async runtime, feature flags, LLM-v3 budgeting, and a Temporal port
are implemented and tested (936 tests, 1 skipped without Docker).** Live
distributed backends remain port-ready adapters wired per the v5.1 rollout plan.

---

## 1. v5.1 → v5.2 delta

| Limitation (v5.1) | v5.2 change | Status |
|-------------------|-------------|--------|
| Synchronous repositories | `AsyncDatabaseManager/Repository/UnitOfWork/TenantAware` | ✅ tested |
| ThreadPool DAG execution | `AsyncWorkflowEngine` (asyncio.TaskGroup, timeouts, cancellation) | ✅ tested |
| No async workers | `AsyncWorkerRuntime` (bounded queue, backpressure, retries) | ✅ tested |
| No feature flag system | `FeatureFlagService` (overrides, rollout, kill switch, reload, audit) | ✅ tested |
| No experiment platform | flags + v5 experiments framework + LLM prompt experiments | ✅ tested |
| LLM v2 only | v3: token budgets, quotas, **adaptive routing**, embedding cache, provider stats | ✅ tested |
| No Temporal integration | `TemporalWorkflowPort/ActivityPort/Persistence` + local runtime | ✅ tested |
| PG adapter, no live test | Testcontainers PG integration test (self-skips offline) | ✅ added |
| Redis/Kafka/OTel/multi-region | port-ready adapters + Helm/compose from v5.1 | ⏳ infra |

---

## 2. Async architecture

```
event loop (uvicorn workers, async endpoints)
  │
  ├─ AsyncWorkflowEngine.run(graph)
  │     for level in topo_levels:                 # dependency order
  │        async with asyncio.TaskGroup() as tg:  # concurrency within level
  │            tasks = [tg.create_task(run_node(n)) for n in level]
  │        # per node: wait_for(timeout) → retries → (sync via to_thread | await)
  │        merge outputs; critical failure → await compensate() (saga)
  │
  ├─ AsyncUnitOfWork → AsyncConnection
  │     SQLite calls run via asyncio.to_thread (loop never blocks)
  │     asyncpg pool drops in behind the same port (no call-site change)
  │
  └─ AsyncWorkerRuntime: asyncio.Queue(maxsize) → N worker coroutines
        backpressure (put_nowait→False when full), retry re-enqueue, graceful stop
```

Blocking I/O is removed from the event loop: DB/file calls go through
`to_thread` today and through native async drivers (asyncpg / redis.asyncio /
httpx.AsyncClient) in production. Cancellation and timeouts propagate via
standard asyncio (`wait_for`, `TaskGroup`, `CancelledError`).

### Sequence — async autonomous workflow
```
POST /api/v2/workflows (async handler)
  start_trace · resolve tenant · check feature flag tenant.<t>.workflow_engine
  AsyncWorkflowEngine.run(graph)
     level1: [search]                      (await, to_thread for sync ATS I/O)
     level2: [score]                       (await)
     level3: [research ‖ analytics]        TaskGroup → concurrent
                research → LLMGateway.complete (AdaptiveRouter picks model by
                           tenant budget; semantic+embedding cache; circuit breaker)
                           BudgetManager.consume(tokens, cost)  ← quota enforced
     level4: [documents]                   (await; per-job parallel via TaskGroup)
  emit WorkflowCompleted → stream consumer groups (notify, learn, analytics)
```

---

## 3. Feature flags & experiments

Resolution order (per `is_enabled`): **kill switch → tenant override →
percentage rollout (stable per-unit hash) → default**. Non-boolean config via
`value()` with tenant precedence. Hot `reload()` swaps the whole flag set
atomically; every mutation is audit-logged. Examples:

```
flags.set_tenant_override("tenant.acme.llm.provider", "acme", "openai")
flags.set_rollout("tenant.beta.semantic_cache", 25)     # 25% gradual
flags.set_kill_switch("tenant.gamma.workflow_engine", True)
```

Experiments compose flags (assignment) + the v5 experiment framework (metrics +
winner) + LLM prompt-registry versions (A/B of prompts) → end-to-end
prompt/strategy experimentation with measured conversion.

---

## 4. Temporal evaluation (deliverable #6)

Both the **custom DAG engine** and **Temporal** are now reachable behind one
port (`TemporalWorkflowPort`), so we can A/B them without rewrites.

| Dimension | Custom DAG engine (built) | Temporal |
|-----------|---------------------------|----------|
| Durability / resume | DB-checkpointed steps; resume() works | Best-in-class event-sourced history, automatic replay |
| Parallel / branch / saga | ✅ (levels, conditions, compensation) | ✅ (built-in) |
| Long-running / timers / signals | limited (would need building) | ✅ first-class (days/months, signals, queries) |
| Operational complexity | low — runs in our pods + Postgres | high — Temporal cluster (server, history/matching, its own DB) |
| Cost | ~0 incremental | cluster infra + ops headcount (or Temporal Cloud $$) |
| Vendor lock-in | none | moderate (workflow code uses temporalio APIs) |
| Visibility tooling | our Grafana/Jaeger | excellent built-in Web UI |
| Time to value now | shipped | weeks of platform integration |

**Recommendation:** **keep the custom DAG engine as the default** for v5.2 — it
covers our workflow shapes (search→score→research→docs→recommend) with low ops
cost and no lock-in. **Adopt Temporal selectively** for genuinely long-running,
human-in-the-loop, or signal-driven flows (e.g. multi-day application tracking
with reminders) by routing those workflow definitions to `TemporalRuntime` via
the port. Revisit a full migration only if (a) we need durable timers/signals
broadly, or (b) replay/visibility needs outgrow our tooling. The port makes this
a per-workflow decision, not a platform rewrite.

---

## 5. Migration & rollout (v5.2 increments)

1. **Async cutover (P0, done in code):** async endpoints + AsyncWorkflowEngine
   behind a flag `tenant.*.async_runtime`; sync paths remain for rollback.
2. **asyncpg (P1):** inject an asyncpg-backed `AsyncDatabaseManager.connect_fn`;
   the to_thread/SQLite path is the dev/test default.
3. **redis.asyncio (P2):** async cache/locks/rate-limit behind the v5.1 ports.
4. **Streams (P3):** swap LocalStreamBroker → RedisStreamsBroker/Kafka.
5. **Flags everywhere:** gate each swap with a per-tenant flag + kill switch so
   rollout is incremental and instantly reversible.

---

## 6. Runbook / incident deltas

- **Async health:** watch event-loop lag + `queue_depth`; if workers saturate,
  scale `AsyncWorkerRuntime.concurrency` / worker pods (HPA).
- **Budget breach:** `BudgetManager.status(tenant)` → if util ≥ downgrade_at the
  AdaptiveRouter already serves the cheap model; raise budget or notify tenant.
- **Provider degradation:** `ProviderStats.should_failover()` → gateway circuit
  opens → fallback provider; flip `tenant.*.llm.provider` if needed.
- **Bad rollout:** set the flag kill switch (instant, audited) — no redeploy.
- **Temporal flows:** if a Temporal-routed workflow stalls, inspect the Temporal
  Web UI; DAG-routed flows use Grafana/Jaeger by `correlation_id`.

All v5.x principles upheld: hexagonal ports (async mirrors sync), DI, immutable
value objects, pure logic; no blocking I/O on the loop; no breaking APIs; every
prior test green.
```
