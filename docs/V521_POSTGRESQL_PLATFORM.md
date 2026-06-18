# Career Intelligence Engine v5.2.1 — Production PostgreSQL Platform (P1)

Status: **toolkit, partition platform, migration framework, backfill/verify,
asyncpg manager, and observability hooks are implemented and tested** (973 tests,
2 Testcontainers tests self-skip without Docker). Live cluster operations
(replication, failover, PITR) are documented + Testcontainers-gated.

---

## 1. Production architecture (C4 — container)

```
                 app pods (asyncpg pools, small)
                         │  $N prepared stmts, statement cache
                         ▼
                  ┌──────────────┐   transaction pooling, max_client_conn=5000
                  │  PgBouncer    │   default_pool_size=50 (medium)
                  └──────┬───────┘
            writes ──────┤────── reads (career_ro)
                         ▼               ▼
                 ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
                 │ pg-primary    │─▶│ pg-replica-1  │  │ pg-replica-2  │
                 │ (RW)          │  │ (RO, stream)  │  │ (RO, stream)  │
                 └──────┬────────┘  └───────────────┘  └───────────────┘
        WAL archiving ──┘ (pgBackRest → object store; PITR)
        failover: Patroni/cloud-managed promotes a replica on primary loss
```

### Deployment (K8s)
```
StatefulSet pg-primary (PVC, anti-affinity per AZ, Patroni)
StatefulSet pg-replica x2 (streaming replication, spread across AZs)
Deployment  pgbouncer x2 (HPA), Service career(RW) / career_ro(RO)
CronJob     partition-maintenance (create next month + apply retention) daily
CronJob     pgbackrest-backup (full weekly, incr hourly; WAL continuous)
sidecars    postgres_exporter, pgbouncer_exporter → Prometheus
```

### Sequence — write with read-after on replica
```
handler → AsyncPgUnitOfWork(read_only=False)
   manager.acquire(read=False) → breaker.allow()? → PgBouncer(career) → primary
   tx.start(); repo.add(...) RETURNING id; tx.commit(); release
handler → AsyncPgUnitOfWork(read_only=True)
   manager.acquire(read=True) → PgBouncer(career_ro) → replica
   repo.get(id)   # tolerate small replication lag; for read-your-write use primary
```

### Failure scenarios
| Failure | Detection | Response |
|---------|-----------|----------|
| Primary loss | health_check fail / Patroni | promote replica → new primary; PgBouncer `career` repoints; app reconnects via pool |
| Replica lag ↑ | `db_replication_lag` gauge | route critical reads to primary; alert; throttle batch reads |
| Pool exhaustion | acquire timeout | PgCircuitBreaker opens → fail fast (backpressure) → 503 + retry-after |
| Network partition | breaker + Patroni quorum | fence minority; avoid split-brain via Patroni DCS quorum |
| Bad migration | smoke + version check | expand-contract makes each phase reversible; `rollback(migration)` |

---

## 2. asyncpg integration

`AsyncPgDatabaseManager` owns a **write pool (primary)** and a **read pool
(replica)** via `asyncpg.create_pool` with statement caching, command timeouts,
and `max_inactive_connection_lifetime` (recycling). `AsyncPgUnitOfWork` acquires
a connection, wraps a transaction (writes), and releases on exit;
`read_only=True` routes to the replica pool. `AsyncPgRepository` builds `$N`
parameterised SQL (unit-tested) and adds `batch_add` (executemany), `copy_insert`
(server-side COPY), keyset `paginate`, and `stream` (constant memory). A circuit
breaker fails fast on pool/primary trouble. **No blocking DB calls** on the event
loop. Activate in prod: `DB_DIALECT=postgres` + `DATABASE_URL`/`REPLICA_URL`,
then inject the manager (the ports are unchanged, so call sites don't move).

## 3. Connection management & pool sizing

| Tier | app pool (min/max) | PgBouncer pool | PG max_connections |
|------|--------------------|----------------|--------------------|
| small | 2 / 10 | 20 | 100 |
| medium | 5 / 20 | 50 | 200 |
| enterprise | 10 / 40 | 200 | ≥ 500 |

`recommend_pool_sizing(tier, cpu_cores, api_replicas)` computes a safe
`recommended_pg_max_connections ≈ api_replicas*pool_max + 4*cores + headroom`.
PgBouncer transaction pooling keeps app pools small; pool exhaustion surfaces as
breaker-open → backpressure (fast 503) rather than pile-ups.

## 4. Partitioned event platform

`events_log`, `workflow_events`, `audit_log`, `llm_costs` → declarative
`PARTITION BY RANGE (created_at)`, **monthly** children
(`events_log_2026_01`, …). `PartitionDDLGenerator` produces parent + child DDL;
`PartitionManager.required_partitions/ensure` creates the current month plus a
lookahead (so writes never hit a missing partition); `PartitionRetentionManager`
computes and drops/detaches partitions past the retention window (archival via
`DETACH`). A daily CronJob runs ensure + retention. Backfill historical data with
`PartitionBackfillService` (keyset batches, row-count + checksum verification,
rollback).

## 5. Indexing strategy

| Index | Type | Why |
|-------|------|-----|
| `(tenant_id, created_at)` | BTREE | tenant time-range scans (most queries) |
| `(tenant_id, workflow_id)` | BTREE | per-tenant workflow lookups |
| `(workflow_id, status)` | BTREE | workflow step/status filters |
| `(created_at, status)` | BRIN | huge append-only tables; tiny, range-friendly |
| `status` partial `WHERE status='FAILED'` | Partial BTREE | hot failures only |
| JSONB `payload` | GIN | ad-hoc payload key search |

Created on the **partitioned parent** so PostgreSQL propagates to every child.

```sql
-- Verify partition pruning + index use:
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM events_log
WHERE tenant_id = 'acme' AND created_at >= '2026-06-01' AND created_at < '2026-07-01';
--  Append
--    ->  Index Scan using idx_events_log_tenant_id_created_at on events_log_2026_06
--          Index Cond: ((tenant_id = 'acme') AND (created_at >= ...) AND (created_at < ...))
--  (only the June partition is scanned — pruning works)
```

## 6. Migration framework (expand-contract, zero-downtime)

`PgMigrationService` tracks versions in `schema_migrations` (idempotent apply,
ordered `apply_all`, `rollback`). `ExpandContractMigration` runs **EXPAND →
backfill → CONTRACT**; the contract phase is **feature-flag gated** so cutover is
controlled and instantly reversible via a kill switch. Each phase is backward
compatible, so old and new code run simultaneously during rollout.

## 7. Backup & recovery (RPO 5m / RTO 30m)

- **WAL archiving** continuous to object storage (pgBackRest) → RPO ≤ 5 min.
- **Backups:** weekly full + hourly incremental; verified by automated restore
  into a scratch instance + `pg_amcheck`.
- **PITR playbook:** `pgbackrest restore --type=time --target='<ts>'` → start →
  promote → repoint PgBouncer → validate row counts/checksums (RTO ≤ 30 min).
- **Failover playbook:** Patroni auto-promotes; manual: promote replica, update
  DCS, repoint `career` service, run `health_check()`, scale app back up.

## 8. Observability

Metrics (in `core.metrics`, scraped at `/api/v2/metrics/prometheus`):
`db_pool_active`, `db_pool_idle`, `db_query_latency` (histogram),
`db_slow_queries`, `db_replication_lag`, `db_partition_size`. `pool_stats()`
publishes pool gauges each scrape. OTel DB spans wrap queries (trace/correlation
propagation) via `core.tracing.span`. Grafana: pool utilization, query latency
p95/p99, replication lag, partition growth, deadlocks; alert on lag > 30s, pool
saturation, error-budget burn (99.95%).

## 9. Testing

- **Pure/logic (always run):** partition naming/ranges/retention, pool sizing,
  circuit breaker, SQL builders, read/write routing.
- **Real against SQLite:** backfill copy + count + checksum + rollback; migration
  apply/idempotent/rollback/expand-contract/feature-flag.
- **Testcontainers (Docker-gated, self-skip):** asyncpg roundtrip + health,
  tenant isolation + optimistic locking on real PostgreSQL.
- **Failover/replication:** breaker + routing unit-tested; full cluster sim is
  Testcontainers/Patroni-based per the playbooks above.

All v5.x APIs, migrations, Docker compatibility, and 973 tests preserved.
```
