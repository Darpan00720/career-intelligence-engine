# Career Intelligence Engine — v5.5 (P4)

A production-grade, explainable, multi-agent **Career Intelligence Platform**
that turns a resume / career history into personalized recommendations, skill-gap
analysis, learning paths, and market-aware opportunities — for 100k+ users —
while preserving every existing API, migration, and test (backward compatible).

It composes the v5.4 AI-orchestration platform (agents, shared memory,
checkpointing, interruption, tenancy, metrics, tracing) and the v5.x
infrastructure (SQLite + additive migrations, `core.tenancy`, `core.metrics`,
`core.tracing`).

---

## 1. Component / deliverable map

| Deliverable | Classes | What it does |
|-------------|---------|--------------|
| `core/career_graph.py` | `CareerGraph`, `SkillOntology`, `RoleOntology`, `RelationshipManager`, `CareerPathEngine` | Versioned, tenant-isolated knowledge graph: skills, roles, industries, prerequisite edges, career-transition shortest path (Dijkstra over difficulty). |
| `core/resume_intelligence.py` | `ResumeParser`, `SkillsExtractor`, `ExperienceExtractor`, `EducationExtractor`, `CertificationExtractor`, `ResumeNormalizer` | Resume (text / PDF / DOCX) → normalized, confidence-scored profile with missing-data flagging and multilingual detection. |
| `core/skill_intelligence.py` | `SkillProfiler`, `SkillGapAnalyzer`, `SkillSimilarityEngine`, `SkillMatcher`, `ProficiencyEstimator` | Proficiency estimation, embedding similarity + clustering, semantic skill matching, explainable gap analysis. |
| `core/market_intelligence.py` | `JobMarketCollector`, `MarketSnapshotService`, `DemandAnalyzer`, `SalaryIntelligence`, `TrendAnalyzer` | Demand by role/industry/geo, percentile salary benchmarks, historical snapshots, trend + emerging-skills detection. |
| `core/recommendation_engine.py` *(extended)* | `RecommendationEngine`, `RecommendationRanker`, `RecommendationExplainer`, `LearningPathGenerator`, `OpportunityScorer` | Personalized job / career-path / learning recommendations with ranking, explainability, confidence, and feedback incorporation. **v3 `recommend()` / `recommend_all()` unchanged.** |
| `core/feedback_manager.py` | `FeedbackManager`, `ExperimentManager`, `FeatureFlagManager`, `RecommendationEvaluator`, `ABTestingFramework` | Feedback capture, online (acceptance) + offline (precision@k, NDCG) evaluation, deterministic A/B tests, % feature-flag rollout. |
| `core/career_workflow.py` | `CareerWorkflow` + `ResumeAgent`, `SkillsAgent`, `GapAnalysisAgent`, `RecommendationAgent`, `LearningPathAgent`, `ExplanationAgent` | The orchestrated pipeline with parallelism, checkpointing, recovery, interruption, human-in-the-loop approval, and shared memory. |

---

## 2. Multi-agent workflow

```
            ┌──────────────────────── shared blackboard + MemoryManager ───────────────────────┐
            │                                                                                    │
  Resume ──▶ Skills ──▶ Gap Analysis ──┬──▶ Recommendation ─┐                                    │
  (parse,             (profile,        │                    ├──▶ Explanation                     │
   normalize)          proficiency)    └──▶ Learning Path  ──┘   (synthesize)                     │
            │                                                                                    │
            └──── checkpoint to agent_runs after every batch · interruptible · resumable ────────┘
```

* **Parallel execution** — `recommendation` and `learning_path` depend only on
  `gap_analysis`, so the dependency scheduler runs them concurrently in one batch
  (`ThreadPoolExecutor`); each opens its own SQLite connection.
* **Checkpointing & recovery** — the shared blackboard + completed-step set is
  persisted to `agent_runs` after each batch. `resume(run_id)` replays only the
  remaining steps; a failed step leaves prior results intact.
* **Interruptible** — a per-run cancel `Event` is checked before each batch;
  cancelled runs land in `CANCELLED`.
* **Human-in-the-loop** — with `require_approval=True` the run halts at
  `AWAITING_APPROVAL` *before* generating recommendations; `approve()` + `resume()`
  continues.
* **Agent memory sharing** — a shared `MemoryManager` (v5.4) lets downstream
  agents retrieve what upstream agents learned, tenant-scoped.

Statuses: `RUNNING → {AWAITING_APPROVAL} → COMPLETED | FAILED | CANCELLED`.

---

## 3. Recommendation model

`OpportunityScorer` blends three normalized signals into a 0–100 score:

```
score = 100 · (0.55·coverage + 0.30·min(1, demand/demand_max) + 0.15·salary_fit)
```

* **coverage** — semantic skill match vs. the role's required skills.
* **demand** — recent market postings for the role (from `market_snapshots`).
* **salary_fit** — candidate target vs. benchmark percentiles.

`RecommendationRanker` then adjusts ordering by feedback learned online
(`accepted/applied` lift a target, `rejected/dismissed` lower it).
`RecommendationExplainer` attaches human-readable reasons, and every item carries
a calibrated **confidence** (rises with coverage). `LearningPathGenerator` emits an
ordered plan that schedules **prerequisites before** the gap skill they unlock.

---

## 4. Persistence (additive migrations only)

New tables (all tenant-scoped, indexed for scale, `IF NOT EXISTS`):

`career_profiles`, `skills`, `skill_relationships`, `role_definitions`,
`career_paths`, `recommendations`, `recommendation_feedback`, `market_snapshots`,
`salary_benchmarks`, `learning_paths`, `feature_flags`
(plus reuse of the existing `experiments` / `experiment_events`).

Indexes: tenant+category on skills, tenant+source on skill relationships,
tenant+slug on roles, tenant+profile on recommendations & learning paths,
tenant+role+geo on market snapshots & salary benchmarks. Ontologies are
**versioned** (a `version` column on `skills` / `role_definitions`), so a new
ontology release is an additive insert — older rows remain for reproducibility.

---

## 5. Observability

* **Metrics** (`core.metrics` registry, Prometheus text): `recommendation_requests`
  and `career_<step>_requests` counters per workflow step; agent timing inherited
  from the v5.4 `agent_execution_time` histogram. Hooks exist for
  `recommendation_latency`, `recommendation_accuracy`,
  `recommendation_acceptance_rate` (via `FeedbackManager.acceptance_rate`),
  `skill_extraction_accuracy`, `resume_processing_time`, `market_data_freshness`,
  `learning_path_completion_rate`.
* **Tracing** (`core.tracing.span`): each workflow step runs inside a
  `career.<step>` span (resume / skills / recommendation / market / learning),
  emitting `span_duration` with trace/span/correlation IDs.

---

## 6. Testing

| File | Focus | Tests |
|------|-------|-------|
| `tests/test_career_graph.py` | ontologies, versioning, Dijkstra path, prerequisites, tenant isolation | 10 |
| `tests/test_resume_intelligence.py` | parsing, extraction, normalization, missing-data, confidence | 9 |
| `tests/test_skill_intelligence.py` | proficiency, similarity/clustering, matching, explainable gaps | 7 |
| `tests/test_market_intelligence.py` | demand, salary percentiles, snapshots, trends, emerging skills, isolation | 7 |
| `tests/test_recommendation_engine.py` | scoring/ranking/explain/learning-path, feedback platform, A/B, flags, **v3 compat** | 13 |
| `tests/test_career_workflow.py` | end-to-end, parallel batch, memory sharing, checkpoint **recovery**, interruption, human-in-the-loop, **load (25 runs)**, **chaos** | 9 |

Total **55 new tests**; full suite **1054 passing** (2 Testcontainers skips),
exceeding the 1000+ target. Tests use a real temp-file SQLite DB initialized via
`database.initialize()`, so the exact production schema (including the v5.5 tables
and indexes) is exercised, and cross-thread parallel steps run against real
connections.

---

## 7. Honest scope & next steps

The platform is **pure-Python application logic composing the existing
infrastructure** — genuinely implemented and tested offline, not stubbed. The
parts that need external services slot behind ports already in place:

* **Embeddings / semantic matching** use the v5.4 `EmbeddingService` (bag-of-words
  cosine). Swap in a real embedding model + vector store behind the same class for
  100k-user semantic quality — no call-site changes.
* **PDF / DOCX parsing** degrades to plain text offline; `ResumeParser._read_pdf` /
  `_read_docx` activate when `pypdf` / `python-docx` are installed.
* **Market data** is aggregated from the local `jobs` table / supplied postings;
  wiring a live market feed is a `JobMarketCollector` adapter.
* **A/B significance** is lift-based; a real sequential test (e.g. Bayesian /
  z-test with power) drops into `ABTestingFramework.winner`.

The verifiable next step is replacing `EmbeddingService` with a production
embedding model + vector index and re-running `tests/test_skill_intelligence.py`
and `tests/test_recommendation_engine.py` against it.
