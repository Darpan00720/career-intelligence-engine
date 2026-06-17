# Recommendation Layer — Authoritative Business Specification

**Status:** RATIFIED — FINAL (Phase 6A revision) · **Version:** 1.0 · **Date:** 2026-06-14
**Implements:** `core/recommendations.py` (validated, 702/702 tests).
**Scope:** presentation / actionable-intelligence layer ONLY. Introduces no
scoring. Does not modify scoring formulas, Opportunity Intelligence formulas,
composite weights, tier thresholds, or metrics — all of those remain governed by
`docs/OPPORTUNITY_INTELLIGENCE_SPEC.md` v1.0.

All metric inputs are integers in [0,100]; `tier`/`composite` come from the
ratified Prioritization spec. Recommendations are fully **deterministic**.

---

## 1. Recommendation categories (authoritative)

| Category | Business purpose | Triggering criteria | User rationale shown |
|---|---|---|---|
| **APPLY_IMMEDIATELY** | Top-tier match; lead the weekly applications | `tier == Tier 1` | "Apply immediately — top-tier match; lead your weekly applications with this." |
| **APPLY_THIS_WEEK** | Strong, winnable role worth near-term effort | `tier == Tier 2` AND `roi >= HIGH_ROI(60)` (and not Stretch) | "Apply this week — strong expected ROI with solid fit." |
| **STRETCH_ROLE** | High strategic value but lower odds — apply selectively | `tier == Tier 2` AND `interview_prob <= LOW_INTERVIEW_PROB(40)` AND `ai_dt_fit >= HIGH_STRATEGIC_FIT(70)` | "Apply selectively as a stretch — invest in networking and targeted prep to offset lower interview odds." |
| **MONITOR** | Not worth active effort now | `tier == Tier 3`, OR `tier == Tier 2` with neither Stretch nor high-ROI condition met | "Monitor — revisit if a stronger signal emerges or capacity allows." |

**Dependencies:** `CompositeScore.tier`, `OpportunityIntelligence.{roi, interview_prob, ai_dt_fit}`.

**Priority order (deterministic, evaluated top-to-bottom — exactly one category per job):**
1. `tier == Tier 1` → **APPLY_IMMEDIATELY**
2. `tier == Tier 3` → **MONITOR**
3. `tier == Tier 2` and Stretch condition → **STRETCH_ROLE**
4. `tier == Tier 2` and `roi >= 60` → **APPLY_THIS_WEEK**
5. otherwise → **MONITOR**

Tier dominates: Tier 1 is always APPLY_IMMEDIATELY; STRETCH is reachable only within Tier 2.

---

## 2. Presentation thresholds (authoritative)

These are **recommendation-layer presentation constants** — distinct from scoring. Normalization: all compared against 0–100 metric scales.

| Constant | Value | Purpose | Depends on | Authoritative? |
|---|---|---|---|---|
| `HIGH_ROI` | 60 | APPLY_THIS_WEEK gate (Tier 2) | `roi` | ✅ |
| `LOW_ROI` | 40 | "Low expected ROI" risk | `roi` | ✅ |
| `LOW_INTERVIEW_PROB` | 40 | STRETCH gate + "Low interview probability" risk + networking prep | `interview_prob` | ✅ |
| `HIGH_STRATEGIC_FIT` | 70 | STRETCH strategic-value gate | `ai_dt_fit` | ✅ |
| `STRONG` | 70 | Strength threshold (mba/ai_dt/brand) | metrics | ✅ |
| `WEAK` | 40 | Risk threshold (sponsorship) | metrics | ✅ |
| `COMPETITIVE_INTERVIEW` | 60 | "Competitive interview probability" strength gate | `interview_prob` | ✅ |
| `FAVORABLE_SPONSORSHIP` | 60 | "Favorable sponsorship likelihood" strength gate | `sponsorship_prob` | ✅ |
| `HIGH_EFFORT` | 60 | "High application effort" risk + extra-prep action | `effort` | ✅ |

Justification: thresholds align to the 0–100 scale with 70 = "strong", 40 = "weak/low", 60 = "good". They affect presentation only; changing them never alters scores, composites, tiers, ranks, or the weekly plan.

---

## 3. Strength-generation rules (authoritative)

Each fires independently; produces 0–5 strengths per job.

| Strength wording | Trigger | Threshold |
|---|---|---|
| "Strong MBA-level fit" | `mba_fit >= STRONG` | 70 |
| "Strong AI / Digital-Transformation alignment" | `ai_dt_fit >= STRONG` | 70 |
| "High employer-brand value" | `brand >= STRONG` | 70 |
| "Competitive interview probability" | `interview_prob >= COMPETITIVE_INTERVIEW` | 60 |
| "Favorable sponsorship likelihood" | `sponsorship_prob >= FAVORABLE_SPONSORSHIP` | 60 |

Deterministic; order fixed as listed. Empty list permitted.

---

## 4. Risk-generation rules (authoritative)

| Risk wording | Trigger | Threshold |
|---|---|---|
| "Low interview probability (stretch)" | `interview_prob <= LOW_INTERVIEW_PROB` | 40 |
| "Sponsorship uncertain" | `sponsorship_prob <= WEAK` | 40 |
| "High application effort" | `effort >= HIGH_EFFORT` | 60 |
| "Low expected ROI" | `roi <= LOW_ROI` | 40 |

Deterministic; order fixed as listed. Empty list permitted.

---

## 5. Preparation-guidance rules (authoritative)

Derive ONLY from: Opportunity Intelligence metrics, role_category, job metadata
(company). At least one action is always produced (the CV action).

| Preparation action | Trigger | Depends on | Rationale |
|---|---|---|---|
| "Tailor CV and cover letter to the role" | **always** | role_category | Aligns application to `{role_category}` |
| "Practice product-sense and product-case interviews" | `role_category == product_management` | role_category | Product role |
| "Prepare 2-3 AI / digital-transformation strategy examples" | `role_category ∈ {ai_strategy, product_management, digital_transformation}` | role_category | AI/Product/Digital track |
| "Prepare a structured business/strategy case" | `role_category == business_strategy` | role_category | Strategy role |
| "Revise people-analytics / workforce metrics and tooling" | `role_category ∈ {people_analytics, hr_analytics, workforce_planning, talent_acquisition}` | role_category | People/Workforce track |
| "Network with alumni/employees at `{company}` to secure a referral" | `interview_prob <= LOW_INTERVIEW_PROB(40)` | interview_prob, company | A referral materially raises low odds |
| "Confirm visa-sponsorship availability before investing time" | `sponsorship_prob <= WEAK(40)` | sponsorship_prob | Sponsorship uncertain |
| "Block extra preparation time; expect strong competition" | `effort >= HIGH_EFFORT(60)` | effort | High estimated effort |

Deterministic and order-stable. No LLM, no scoring, no new metrics. `role_category` falls back to `"unknown"` (only the CV action fires).

---

## 6. State invariants (ratified)

### 6.1 Recommendation-count invariant — RATIFIED: Option A

Two guarantees apply at different layers:

- **Normal-pipeline guarantee (authoritative):** `len(recommendations) == len(prioritized)`.
  In standard graph execution every prioritized job originates from an
  `OpportunityIntelligence` record (both are produced from `scored_jobs`), so
  exactly one Recommendation is generated per prioritized job.
- **Defensive guarantee (module contract):** `len(recommendations) <= len(prioritized)`.
  `generate_recommendations` defensively skips a prioritized job whose
  `intelligence` record is absent. This can only arise from external misuse,
  partial state corruption, or direct module invocation outside the normal graph
  path — never in standard execution.

These are consistent: equality is the pipeline guarantee; `<=` is the safe lower
bound the function honors under abnormal input. Missing intelligence is treated as
a defensive skip, NOT a hard error (no `StateValidationError` is raised).

### 6.2 Per-recommendation invariants

- `recommendations` exists after the recommendations node.
- Every `Recommendation`:
  - references a `job_id` that exists in `prioritized` (enforced by the
    `CareerStateModel` cross-reference validator);
  - carries a valid `RecommendationType` (enum-enforced);
  - contains ≥ 1 `PreparationAction` (schema `min_length=1`);
  - output preserves **ranking order** (`generate_recommendations` iterates
    `prioritized` sorted by `rank`).

---

## 7. Architecture constraints (ratified)

- Business logic lives ONLY in `core/recommendations.py`.
- The graph wrapper (`graph/nodes.py::recommendations_node`) may only: invoke
  `generate_recommendations`, map inputs/outputs to schemas, append `AgentError`,
  catch exceptions.
- The wrapper may NOT: introduce thresholds, categorization logic, generate
  recommendations directly, or duplicate preparation logic.
- Schemas: `Recommendation`, `RecommendationType`, `PreparationAction`
  (`schemas/recommendations.py`) — fixed contracts.

---

## 8. Deterministic vs derived decision matrix

| Element | Source | Determinism |
|---|---|---|
| Category | tier + roi/interview_prob/ai_dt_fit thresholds | Deterministic |
| Strengths | metric thresholds | Deterministic |
| Risks | metric thresholds | Deterministic |
| Preparation | role_category + metric thresholds + company metadata | Deterministic |
| Reason text | tier + composite + metrics (templated) | Deterministic |
| Ordering | prioritized rank | Deterministic |

**No LLM in the recommendation layer.** It consumes the already-computed
Opportunity Intelligence values (two of which were LLM-generated upstream) purely
deterministically. Output is therefore fully reproducible and offline-safe.

---

## 9. Notes for Phase 7
Phase 7 (output experience / exports / dashboards / API) must consume these
`Recommendation` objects as-is via thin wrappers and MUST NOT re-derive
categories, thresholds, strengths, risks, or preparation actions. Any change to a
threshold or rule above is a spec revision (v1.1+), not an implementation detail.

**These specifications are hereby authoritative.** Phase 7 may proceed.
