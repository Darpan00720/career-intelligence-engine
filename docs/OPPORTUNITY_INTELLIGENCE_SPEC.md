# Opportunity Intelligence & Prioritization — Authoritative Business Specification

**Status:** RATIFIED (Phase 4A) · **Version:** 1.0 · **Date:** 2026-06-14
**Supersedes:** Phase-3A inert stubs and the Phase-3 inferred adapters (both non-authoritative).

This document is the single source of truth for implementing
`core/opportunity_intelligence.py` and `core/prioritization.py` in Phase 5.
Implementation MUST preserve these definitions exactly. All formulas are
derived from **existing ratified inputs** (`score_job` components, the
eligibility engine, company tiers, `COMPOSITE_WEIGHTS`).

Ratified decisions (Phase 4A):
1. Interview Probability + Application Effort → **LLM (mockable)**; all other metrics deterministic.
2. Sponsorship Probability → **deterministic from the eligibility engine**.
3. Tier cutoffs → **Tier 1 ≥ 65, Tier 2 ≥ 50, Tier 3 < 50** (composite 0–100).
4. Composite weights → **ratified as-is** (in-code `COMPOSITE_WEIGHTS`).

All metric outputs are integers in **[0, 100]** unless stated otherwise.

---

## 1. Opportunity Intelligence — the 8 metrics

Inputs available per job at assessment time:
- `ScoredJob.components`: `track_alignment` (0–30), `skill_match` (0–25),
  `mba_relevance` (0–15), `company_quality` (0–15), `intl_friendliness` (0–10),
  `pivot_bonus` (0–5); `ScoredJob.total_score` (0–100); `semantic_similarity` (0–1).
- Eligibility engine (jobs table): `visa_accessibility` (0–40),
  `language_accessibility` (0–40), `english_environment` (0–10),
  `international_signals` (0–10), `eligibility_score` (0–100).
- `company_eligibility.lookup_company(company)` → `CompanyEligibilityProfile`
  with `visa_friendliness_score` (0–10), `english_environment_score` (0–10),
  `international_student_score` (0–10), `mba_friendliness_score` (0–10).
- `CareerProfile`, `IngestedJob` (title/company/location), `ClassifiedJob` (role_category/track).

### 1. MBA Fit — DETERMINISTIC
- **Purpose:** relevance of the role to an MBA-level strategic profile.
- **Inputs:** `mba_relevance` (0–15).
- **Formula:** `mba_fit = round(mba_relevance / 15 * 100)`.
- **Normalization:** linear 0–100. **Thresholds:** none.

### 2. AI / Digital Transformation Fit — DETERMINISTIC
- **Purpose:** alignment to the candidate's primary pivot tracks (AI/Product/Strategy/Digital Transformation).
- **Inputs:** `track_alignment` (0–30) — already encodes the ratified PRIORITY TRACKS ordering in `scoring_prompt.txt` / `TRACK_ALIGNMENT`.
- **Formula:** `ai_dt_fit = round(track_alignment / 30 * 100)`.
- **Normalization:** linear 0–100. **Thresholds:** none.

### 3. Brand — DETERMINISTIC
- **Purpose:** employer brand / prestige signal.
- **Inputs:** `company_quality` (0–15) — tier-based (`company_tiers.json`) + watchlist.
- **Formula:** `brand = round(company_quality / 15 * 100)`.
- **Normalization:** linear 0–100. **Thresholds:** none.

### 4. Interview Probability — LLM (mockable)
- **Purpose:** candid probability the candidate is competitive enough to get an interview, accounting for experience/seniority/skill gaps. Must NOT be inflated for aspirational roles.
- **Inputs (to prompt):** `CareerProfile` (strengths, gaps, years), job title + description, `ScoredJob.total_score`, `role_category`, `track`.
- **Generation:** structured LLM output `InterviewAssessment{interview_probability: int[0,100], rationale: str}` via the existing mockable hook (`graph/providers.set_claude_hook` pattern). In tests the hook is mocked → deterministic & offline.
- **Calibration guidance (in prompt, not a code formula):** senior/staff/principal roles and roles requiring deep AI-implementation or engineering depth must receive materially reduced probability; roles leveraging the candidate's TA/people/analytics strengths receive raised probability.
- **Normalization:** LLM returns 0–100 directly (schema-clamped). **Thresholds:** none at metric level.
- **Failure handling:** on LLM error, fall back to `interview_probability = total_score` and flag `OpportunityIntelligence.status = "degraded"`.

### 5. Sponsorship Probability — DETERMINISTIC (eligibility engine)
- **Purpose:** realistic probability the company can hire an international candidate needing work-authorization support.
- **Inputs:** `visa_accessibility` (0–40, JD-level), `CompanyEligibilityProfile.visa_friendliness_score` (0–10, company-level), `intl_friendliness` (0–10, scorer).
- **Formula:**
  ```
  sponsorship_prob = round(
      0.50 * (visa_accessibility / 40 * 100)
    + 0.30 * (visa_friendliness_score / 10 * 100)
    + 0.20 * (intl_friendliness / 10 * 100)
  )
  ```
- **Normalization:** each term scaled to 0–100; weights sum to 1.0 → result 0–100.
- **Thresholds:** none at metric level. Fallback company profile (`NEUTRAL_PROFILE`, score 5) applies automatically when the company is unknown.

### 6. Career Pivot Value — DETERMINISTIC
- **Purpose:** strategic value of the role to the candidate's pivot, independent of winnability.
- **Inputs:** `track_alignment` (0–30) (PRIORITY TRACKS).
- **Formula:** `pivot_value = round(track_alignment / 30 * 100)`.
- **Normalization:** linear 0–100. **Thresholds:** none.
- *(Note: identical formula to AI/DT Fit by ratification — both express track alignment. Kept as separate fields because the composite weights them differently and a future spec may diverge them.)*

### 7. Application Effort — LLM (mockable)
- **Purpose:** relative effort to apply competitively (networking, interview prep, technical prep, expected competition, language). Higher = more effort.
- **Inputs (to prompt):** job title/description, company, `ScoredJob.total_score`, language signals, `CareerProfile.gaps`.
- **Generation:** structured LLM output `EffortAssessment{application_effort: int[0,100], rationale: str}` via the mockable hook (mocked in tests).
- **Normalization:** 0–100 (schema-clamped). **Thresholds:** none.
- **Failure handling:** on LLM error, fall back to `application_effort = 100 - interview_probability` and flag `status = "degraded"`.

### 8. Expected ROI — DETERMINISTIC (composed)
- **Purpose:** expected value of applying = realistic chance of progress × strategic value, discounted by effort.
- **Inputs:** `interview_prob`, `pivot_value`, `sponsorship_prob`, `effort` (all 0–100).
- **Formula:**
  ```
  base = 0.50 * interview_prob + 0.30 * pivot_value + 0.20 * sponsorship_prob
  roi  = round(base * (1 - 0.30 * effort / 100))
  ```
  (Effort applies up to a 30% discount on expected value.)
- **Normalization:** result clamped to [0, 100]. **Thresholds:** none.

---

## 2. Prioritization

### Composite Score
- **Weights (ratified as-is, `schemas/planning.COMPOSITE_WEIGHTS`, sum = 1.0):**
  interview_prob 0.30 · mba_fit 0.20 · ai_dt_fit 0.20 · sponsorship_prob 0.15 · brand 0.10 · pivot_value 0.05.
- **Formula:**
  ```
  composite = 0.30*interview_prob + 0.20*mba_fit + 0.20*ai_dt_fit
            + 0.15*sponsorship_prob + 0.10*brand + 0.05*pivot_value
  ```
- **Normalization:** each metric 0–100, weights sum 1.0 → composite ∈ [0, 100]. Round to 2 decimals.
- *(Effort is intentionally NOT in the composite; it is captured inside ROI.)*

### Tiering (on composite)
| Tier | Cutoff | Meaning |
|---|---|---|
| **Tier 1** | composite ≥ 65 | Apply immediately |
| **Tier 2** | 50 ≤ composite < 65 | Apply if capacity allows |
| **Tier 3** | composite < 50 | Monitor only |

### Ranking
- **Primary order:** composite **descending**.
- **Tie-breakers (in order):** (1) `interview_prob` desc, (2) `ScoredJob.total_score` desc, (3) `job_id` asc.
- Ranks are 1-based and contiguous.

### Application Planning
- **this_week:** the top **5** by rank, **subject to Tier ≥ 2** (never surface a Tier-3 role as "apply this week"). If fewer than 5 roles are Tier 1/2, `this_week` contains only those (may be < 5). Hard cap = `MAX_THIS_WEEK = 5`.
- **next_week:** the next 5 by rank (ranks 6–10) that are Tier ≥ 2.
- **Capacity constraint:** weekly application volume must not exceed `config.MAX_APPLICATIONS_PER_WEEK = 15` across this_week + next_week planning.
- **WeeklyPlan** carries `this_week` and `next_week` as `ApplyItem` lists; each `ApplyItem` sources company/title/location from the matching `IngestedJob` and `interview_prob`/`roi` from its `OpportunityIntelligence`.

---

## 3. Implementation architecture (ratified interfaces)

```python
# core/opportunity_intelligence.py
def assess(profile: CareerProfile, job: IngestedJob,
           classified: ClassifiedJob, score: ScoredJob,
           company_profile: CompanyEligibilityProfile | None = None,
           claude_hook=None) -> OpportunityIntelligence:
    """Deterministic metrics from `score`/eligibility; interview_prob & effort
    via claude_hook (mockable). Returns a fully-populated OpportunityIntelligence."""

# core/prioritization.py
def composite(intelligence: OpportunityIntelligence) -> float: ...
def assign_tier(composite_score: float) -> Tier: ...      # 65 / 50 cutoffs
def prioritize(intelligence: list[OpportunityIntelligence],
               jobs: dict[int, IngestedJob]) -> WeeklyPlan: ...
```
- `assess` may call `company_eligibility.lookup_company` if `company_profile` not supplied.
- The graph wraps these at the existing seams `opportunity_intel_node` / `prioritization_node` (replacing the inert stubs) — thin wrappers only.
- `eligibility` fields (`visa_accessibility`, `intl_friendliness`) are read from the job record provided by the JobProvider.

---

## 4. Deterministic vs LLM decision matrix (ratified)

| Metric | Source | Test behavior |
|---|---|---|
| MBA Fit | Deterministic | offline |
| AI/DT Fit | Deterministic | offline |
| Brand | Deterministic | offline |
| **Interview Probability** | **LLM (mockable)** | hook mocked → offline/deterministic |
| Sponsorship Probability | Deterministic (eligibility engine) | offline |
| Career Pivot Value | Deterministic | offline |
| **Application Effort** | **LLM (mockable)** | hook mocked → offline/deterministic |
| Expected ROI | Deterministic (composed) | offline |
| Composite / Tier / Rank / Plan | Deterministic | offline |

LLM metrics use one structured call returning both `interview_probability` and
`application_effort` (one prompt, e.g. `prompts/opportunity_intel_prompt.txt`),
routed through the existing mockable hook so the full graph remains offline and
deterministic in tests.

---

## 5. Schemas (already exist — no change required)
`OpportunityIntelligence`, `SkillGap`, `GapCategory` (`schemas/intelligence.py`);
`CompositeScore`, `ApplyItem`, `WeeklyPlan`, `COMPOSITE_WEIGHTS`, `MAX_THIS_WEEK`
(`schemas/planning.py`); `Tier` (`schemas/control.py`).
New (Phase 5) structured LLM contracts to add: `InterviewAssessment`,
`EffortAssessment` (or a combined `IntelLLMAssessment`).

---

## 6. Modules to implement in Phase 5
1. `core/opportunity_intelligence.py` — `assess(...)`.
2. `core/prioritization.py` — `composite`, `assign_tier`, `prioritize`.
3. `prompts/opportunity_intel_prompt.txt` — interview_prob + effort (structured output).
4. Wrap at `graph/nodes.py` `opportunity_intel_node` / `prioritization_node` (thin wrappers).
5. Schemas `InterviewAssessment` / `EffortAssessment` in `schemas/intelligence.py`.

**Phase 5 is UNBLOCKED.** Implementation must preserve every formula, weight,
threshold, and rule above exactly, as thin wrappers.
