# Career Intelligence Engine v5.1 — Cloud-Native Architecture & Operations

Status: **foundation implemented & tested (906 tests green)**; distributed
backends (PostgreSQL/Redis/Kafka/K8s/OTel) are **port-ready adapters + artifacts**
to be wired against real infrastructure per the rollout plan below.

---

## 1. Current (v5) → target (v5.1)

| Concern        | v5 (now)              | v5.1 target                          | Status |
|----------------|-----------------------|--------------------------------------|--------|
| Persistence    | SQLite, direct conns  | PG pool, replicas, partitions        | Repository/UoW port ✅, PG adapter ⏳ |
| Cache          | in-memory             | Redis cluster + Sentinel             | RedisCacheBackend ⏳ |
| Events         | in-process bus        | Kafka/Redis Streams, consumer groups | Stream port + local broker ✅, Redis adapter ⏳ |
| Workflows      | linear, durable       | **DAG** (parallel/branch/saga)       | ✅ implemented |
| Workers        | local pool            | distributed brokered workers         | port ✅, broker ⏳ |
| Observability  | local registry        | OTel + Prometheus + Jaeger           | facade ✅, OTel adapter ⏳ |
| LLM            | gateway + cost        | registry, router, semantic cache, multi-provider | ✅ implemented |
| ML             | bandits/feature-imp   | feature store + model registry + LTR | ✅ implemented |
| Deploy         | single container      | Helm/K8s, HPA, PDB, multi-AZ         | charts + compose ✅ |

✅ = code + tests in this repo. ⏳ = adapter/artifact present, needs live infra.

---

## 2. C4 — Container view (ASCII)

```
                       ┌──────────────── Ingress / LB ────────────────┐
                       │            (rolling, multi-AZ)                │
                       └───────┬───────────────────────┬───────────────┘
                          /api (v1)               /api/v2 (tenant hdr, RBAC, rate-limit)
                               ▼                       ▼
   ┌──────────────────── API pods (HPA 3–20) ──────────────────────┐
   │ Auth/RBAC · Tenancy ctx · Tracing(trace/span/correlation)      │
   │ DAG Workflow Engine ── Orchestrator ── LLM Gateway v2          │
   └───┬──────────┬───────────┬───────────┬──────────┬─────────────┘
       │          │           │           │          │   (hexagonal ports)
   Repository  CacheBackend  Broker    Worker pool  Provider/Router
   /UnitOfWork  │            │          │            │
       │        │            │          │            │
   ┌───▼──┐  ┌──▼──┐    ┌────▼────┐  ┌──▼───┐   ┌────▼─────────────┐
   │ PG    │  │Redis│    │ Kafka / │  │Worker│   │Anthropic/OpenAI/ │
   │primary│  │+Sent│    │ Streams │  │ pods │   │Azure (failover)  │
   │+repl. │  └─────┘    │+DLQ topic│ │HPA   │   └──────────────────┘
   └───────┘             └─────────┘  └──────┘
        observability ▶ OTel Collector ▶ Prometheus + Grafana + Jaeger
```

## 3. Deployment view (K8s)

```
Namespace: career
  Deployment api      (HPA 3–20, PDB minAvailable=2, readiness=/ready)
  Deployment worker   (HPA 4–50, PDB minAvailable=1)
  StatefulSet postgres-primary + 2 read replicas (PVC, anti-affinity per AZ)
  StatefulSet redis   (3 nodes + Sentinel)
  StatefulSet kafka   (3 brokers, RF=3, min.insync=2)
  prometheus / grafana / jaeger / otel-collector
  ExternalSecrets ▶ Vault (JWT_SECRET, SECRETS_KEY, DB creds, LLM keys)
```

## 4. Sequence — autonomous workflow (happy path)

```
Client ─POST /api/v2/workflows─▶ API
  API: verify JWT (RS256/JWKS) ▶ resolve tenant ▶ start_trace
  API ▶ DAGWorkflowEngine.start(defn, tenant)
        persist WorkflowRun(PENDING→RUNNING)  [PG, partitioned by created_at]
  ExecutionPlanner ▶ levels = topo_sort(graph)
  for level in levels (parallel):
     for node: LLMGateway.complete(...)  ──▶ semantic cache? ──hit─▶ return
                                          └─miss─▶ router.route(task) ▶ provider
                                                  (circuit breaker + failover)
                                                  record cost (tenant/workflow/agent)
     persist WorkflowStep(COMPLETED) + emit node_completed
  emit WORKFLOW_COMPLETED ▶ Kafka topic (trace/correlation/causation/workflow/tenant)
  consumers: NotificationAgent, LearningEngine, AnalyticsRefresh (consumer groups)
```

## 5. Sequence — crash recovery

```
worker crash mid-DAG ─▶ run left in RUNNING with some steps COMPLETED
operator/scheduler ─▶ DAGWorkflowEngine.resume(run_id, defn)
  reload COMPLETED step outputs into context
  re-plan; skip COMPLETED nodes; execute remainder
  → COMPLETED / PARTIAL_SUCCESS    (idempotency keys prevent dup side-effects)
```

---

## 6. Migration strategy (zero-downtime)

1. **Dual-write nothing; swap the port.** All data access already funnels through
   `database.get_connection` / `core.persistence`. Postgres is enabled by setting
   `DB_DIALECT=postgres` and injecting `pg_adapter.build_pg_manager(DATABASE_URL)`.
2. **Schema parity.** Re-create the v5 schema in PG (same tables; `TEXT`→`JSONB`
   for `*payload/detail/config`). Existing additive migrations are preserved.
3. **Backfill.** One-shot copy SQLite→PG (`pgloader` or a scripted dump); verify
   row counts + checksums per table.
4. **Cutover.** Flip `DB_DIALECT`, run read-only canary, then take writes.
   Rollback = flip back (SQLite retained read-only for the cutover window).
5. **Partitioning.** Convert high-volume tables (`events_log`, `audit_log`,
   `workflow_events`, `llm_costs`) to monthly `RANGE (created_at)` partitions;
   indexes on `(tenant_id)`, `(workflow_id)`, `(created_at)`, `(status)`.
6. **Cache/events/workers** swap behind their ports the same way (Redis, Kafka,
   brokered workers) — one subsystem at a time, each independently revertible.

## 7. Phased rollout

- **P0 (done):** DAG engine, persistence/UoW, event envelope v2 + streams, LLM v2,
  ML platform — all tested on SQLite/local.
- **P1:** PostgreSQL (pool + replicas + partitions) behind the persistence port.
- **P2:** Redis (cache, Redlock, rate limiter, sessions) behind cache/limiter ports.
- **P3:** Kafka/Redis-Streams broker behind the stream port; move WorkflowCompleted/
  Notification flows to consumer groups.
- **P4:** Brokered/distributed workers + autoscaling.
- **P5:** OTel collector + Grafana SLO dashboards + Jaeger.
- **P6:** OIDC/RS256/JWKS + Vault/KMS; SOC2/GDPR workflows hardening.
- **P7:** Multi-AZ HA + DR (PITR, cross-region replicas) to 99.95%.

---

## 8. Operations runbook (essentials)

- **Deploy:** `helm upgrade --install career-engine deploy/helm/career-engine
  -f values-prod.yaml` (rolling, `maxUnavailable=0`).
- **Health:** `/health` (liveness), `/ready` (deps), `/api/v2/metrics` (snapshot),
  `/api/v2/metrics/prometheus` (scrape).
- **Scale:** HPA on CPU; for LLM-bound load also scale on `queue_depth` gauge.
- **DB failover:** promote a read replica (Patroni/cloud-managed); app reconnects
  via pool; read traffic already routed to replicas.
- **Redis failover:** Sentinel promotes; clients rediscover master.
- **Backups:** PG continuous WAL archiving (PITR, **RPO ≤ 5 min**); nightly base
  backups; restore drill monthly (**RTO ≤ 30 min**).
- **Retention/GDPR:** cron `security.apply_retention()`; erasure via
  `security.delete_tenant_data(tenant_id)`.
- **Cost:** `cost_intelligence.full_report()` daily; alert on per-tenant burn.

## 9. Incident response

| Signal | First action | Mitigation |
|--------|--------------|------------|
| LLM error rate ↑ | check `CircuitBreaker` state / provider status | breaker auto-opens → fallback provider; raise cache TTL |
| Queue depth ↑ (backpressure) | inspect `queue_depth` gauge | scale workers (HPA), shed low-priority tasks |
| DB primary down | confirm replica health | promote replica; app reconnects; post-incident PITR check |
| Event lag ↑ | consumer-group offsets vs head | add consumers/partitions; drain DLQ after fix |
| Auth failures spike | check JWKS rotation / clock skew | roll back key rotation; revoke compromised `jti` |
| Cost spike | `cost_intelligence.optimization_recommendations()` | route to cheaper model; enforce tenant quota |

Every event/log carries `trace_id` + `correlation_id` + `tenant_id` → pivot in
Jaeger/Grafana by correlation ID to reconstruct a request end-to-end.

---

## 10. Architecture principles upheld

Hexagonal ports (persistence, cache, broker, provider, worker), DDD-aligned
modules, CQRS-friendly read models (analytics/feedback are read-only
projections), event envelope ready for event sourcing, dependency injection
everywhere (agents/gateway/repositories), immutable value objects (Event,
PromptVersion), pure business logic isolated from I/O. No breaking API changes;
`/api` v1 and all 906 tests remain green.
```
