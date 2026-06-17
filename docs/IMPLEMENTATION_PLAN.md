# Implementation Plan

## How to Read This Document

This plan is organized into 6 phases. Each phase builds on the previous one. Nothing in a later phase is started until the earlier phase is working and tested. Every phase ends with a real, testable deliverable.

**Total estimated build time:** 5–6 weeks of focused sessions.

---

## Architectural Constraints

These two constraints apply to every phase of this build. They are checked during code review at the end of every phase. A phase is not complete if either constraint is violated.

---

### Constraint 1: No Hardcoded Prompts

**Rule:** No Python file may contain prompt text. All Claude instructions must be loaded from files in `prompts/` at runtime.

**Check at every phase:** `grep -r "You are" agents/` must return zero results. Any match means a prompt has been hardcoded and must be moved to `prompts/`.

**All four prompt files are created in Phase 1** as stubs, even before the agents that use them are built. This ensures the file structure is always complete and correct from the start.

| Prompt file | Created (stub) | Filled in (full content) |
|---|---|---|
| `prompts/scoring_prompt.txt` | Phase 1 | Phase 3 |
| `prompts/company_research_prompt.txt` | Phase 1 | Phase 3 |
| `prompts/resume_prompt.txt` | Phase 1 | Phase 4 |
| `prompts/cover_letter_prompt.txt` | Phase 1 | Phase 4 |

---

### Constraint 3: Skill Dictionary Must Gate Scoring

**Rule:** The Scoring Agent must load `data/skill_dictionary.json` and use it to normalize job description keywords before sending anything to Claude. Claude must never be asked to score raw job text against raw profile text — the structured skill mapping step is non-negotiable.

**Check at every phase:** The `scores` table must contain a non-empty `matched_categories` column for every scored job. An empty column means the skill dictionary step was skipped.

**Why:** Without the dictionary, two identical jobs phrased differently produce different scores. With it, "headcount planning," "workforce scenarios," and "FTE modeling" all map to `workforce_planning` before Claude sees them. Scores become consistent, comparable, and explainable.

---

### Constraint 2: No Hardcoded Candidate Data

**Rule:** No Python file may contain personal information about the candidate. All profile data must be loaded from `data/candidate_profile.json` via `core/profile_loader.py`.

**Check at every phase:** `grep -r "POLIMI\|Darpan\|Milan\|Talent Acquisition" agents/` must return zero results. Any match means profile data has been hardcoded and must be removed.

**`data/candidate_profile.json` is populated in Phase 1** before any agent is built. Every agent built in Phases 2–6 uses `profile_loader.load()` — never a hardcoded variable.

---

### Constraint 4: Role Dictionary Must Gate Title Classification

**Rule:** The Scoring Agent must load `data/role_dictionary.json` to classify the job title into a standardized role category before any skill matching begins. Role category flows into Claude's structured input — it is never derived from the raw title string inside any Python file.

**Check at every phase:** The `scores` table must contain a non-empty `role_category` column for every scored job. An empty value means the role dictionary step was skipped.

**Why:** Without normalization, "Associate Product Manager" and "Product Owner" score against different criteria even though they are the same job. Role category also controls how skill categories are weighted — a product management role weights `product_management` and `ai_strategy` higher; an HR analytics role weights `people_analytics` and `hr_analytics` higher. This makes the Role Fit sub-score meaningful and consistent.

---

## Database Design

The database is the foundation everything else reads from and writes to. It is designed in full before any code is written.

### What is a Database?
A database is an organized collection of tables — like Excel sheets — that can be linked together. This project uses SQLite: the entire database is one file called `career_agent.db` on your computer. No server, no setup.

---

### Table 1: `jobs`
Every job the Search Agent finds.

| Column | Type | Description |
|---|---|---|
| id | Integer (auto) | Unique ID — assigned automatically |
| title | Text | e.g. "AI Strategy Intern" |
| company | Text | e.g. "Siemens" |
| location | Text | City, country, or "Remote" |
| job_board | Text | LinkedIn, Indeed, Glassdoor, etc. |
| url | Text | Link to the original posting |
| description | Text | Full job description text |
| posted_date | Date | When the job was posted |
| fetched_date | Date | When the agent found it |
| is_expired | Boolean | True if the posting has been removed |
| raw_data | JSON | Full original data from the job board |

---

### Table 2: `scores`
One row per job — the AI-generated score and explanation.

| Column | Type | Description |
|---|---|---|
| id | Integer (auto) | Unique ID |
| job_id | Integer | Links to `jobs` |
| role_category | Text | Normalized role category from role_dictionary.json (e.g. `product_management`) |
| total_score | Integer | Overall score 0–100 |
| role_fit | Integer | Sub-score: title match (0–30) |
| skills_match | Integer | Sub-score: skills match (0–40) |
| location_fit | Integer | Sub-score: location compatibility (0–15) |
| seniority_fit | Integer | Sub-score: level appropriateness (0–15) |
| explanation | Text | Claude's plain-English explanation |
| matched_categories | JSON | List of skill_dictionary categories matched, with match type (direct/adjacent/none) |
| scored_at | DateTime | When scoring happened |
| prompt_version | Text | Which version of scoring_prompt.txt was used |
| dictionary_version | Text | Which version of skill_dictionary.json was used |
| role_dictionary_version | Text | Which version of role_dictionary.json was used |

Note: `prompt_version`, `dictionary_version`, and `role_dictionary_version` together make every score reproducible. If you update any of the three, you can re-score old jobs and compare results. `role_category` shows which role type was scored; `matched_categories` shows exactly which skills drove the Skills Match sub-score.

---

### Table 3: `company_research`
Research dossier for each approved company.

| Column | Type | Description |
|---|---|---|
| id | Integer (auto) | Unique ID |
| job_id | Integer | Links to `jobs` |
| company | Text | Company name |
| mission | Text | Mission and values in the company's own words |
| recent_news | Text | Funding, products, partnerships, leadership changes |
| culture_notes | Text | What the company says about its people and culture |
| key_people | Text | Relevant leadership or team members |
| talking_points | Text | 3–5 specific angles to reference in documents |
| researched_at | DateTime | When the research was run |
| research_quality | Integer | Quality score 0–10 assigned by Claude after research |
| research_quality_tier | Text | Derived from score: "Poor" (0–3), "Moderate" (4–6), "Strong" (7–10) |
| personalization_mode | Text | Set from tier: "generic", "company_aware", "deep_personalization" |
| prompt_version | Text | Which version of company_research_prompt.txt was used |

---

### Table 4: `documents`
Every resume and cover letter generated.

| Column | Type | Description |
|---|---|---|
| id | Integer (auto) | Unique ID |
| job_id | Integer | Links to `jobs` |
| type | Text | "resume" or "cover_letter" |
| file_path | Text | Where the file is saved |
| file_name | Text | e.g. `Resume_Darpan_Siemens_AIStrategyIntern_20260609.docx` |
| generated_at | DateTime | When it was created |
| word_count | Integer | Length of the document |
| version | Integer | v1, v2 — allows regeneration and comparison |
| langgraph_run_id | Text | Which LangGraph run produced this document |
| prompt_version | Text | Which versions of resume/cover_letter prompt were used |

---

### Table 5: `applications`
Status tracking for every job you pursue.

| Column | Type | Description |
|---|---|---|
| id | Integer (auto) | Unique ID |
| job_id | Integer | Links to `jobs` |
| status | Text | Found → Shortlisted → Applied → Interview → Offer → Rejected → Withdrawn |
| applied_date | Date | When you submitted |
| contact_name | Text | Recruiter or hiring manager |
| notes | Text | Free text |
| next_action | Text | What to do next |
| next_action_date | Date | When to do it |
| outcome | Text | Final result |
| last_updated | DateTime | When this row last changed |

---

### Table 6: `searches`
Log of every search run.

| Column | Type | Description |
|---|---|---|
| id | Integer (auto) | Unique ID |
| query | Text | Search query used |
| job_board | Text | Which board was searched |
| jobs_found | Integer | How many jobs this search returned |
| searched_at | DateTime | When the search ran |
| filters_used | JSON | Location, date, level filters applied |

---

### How the Tables Connect

```
searches ─────────────────────┐
                              │ (one search finds many jobs)
jobs ←────────────────────────┘
  │
  ├──── scores              (one job → one score row)
  ├──── company_research    (one job → one research dossier)
  ├──── documents           (one job → many documents: resume v1, v2, cover letter...)
  └──── applications        (one job → one application record)
```

---

## `data/candidate_profile.json` — Single Source of Truth

**Path:** `data/candidate_profile.json`

This file is populated in Phase 1 and read by every agent that needs your information. No agent stores profile data internally.

### Full structure

```json
{
  "personal":    { name, email, phone, linkedin_url, location, languages[] },
  "education":   [{ institution, degree, field, dates, key_coursework[] }],
  "experience":  [{ title, company, location, dates,
                    responsibilities[], skills_used[], achievements[] }],
  "skills":      { hr_core[], analytics_tools[], ai_and_digital[],
                    strategy_and_business[], soft_skills[], certifications[] },
  "target_roles": { track_1_product_and_strategy[], track_2_strategic_hr[],
                     seniority_preference[], contract_types[] },
  "target_geography": { based_in, preferred_locations[], open_to_all_europe,
                         remote_ok, hybrid_ok, relocation_ok },
  "career_goals":  { value_proposition, preferred_industries[], preferred_company_sizes[] },
  "application_preferences": { min_score_threshold, max_applications_per_week,
                                 exclude_companies[], exclude_industries[] }
}
```

### The `achievements` field is the most important

Every bullet point in every resume Claude writes will come from `experience[].achievements[]`. The richer and more quantified this field is, the better every document.

| Weak (vague) | Strong (quantified) |
|---|---|
| "Improved hiring process" | "Reduced time-to-hire by 30% across 5 business units" |
| "Managed workforce planning" | "Built workforce planning model covering 1,200 FTEs across 3 markets" |
| "Led HR analytics project" | "Delivered people analytics dashboard adopted by 8 senior HR leaders" |

---

## Phase 1: Foundation
**Goal:** The entire skeleton exists. Folder structure is complete. Database works. Claude responds. Profile is populated. All four prompt stubs exist.

### Tasks

**Structure:**
1. Create all folders: `agents/`, `core/`, `prompts/`, `data/`, `data/profile/`, `data/logs/`, `config/`, `templates/`, `outputs/resumes/`, `outputs/cover_letters/`, `outputs/exports/`, `tests/`, `docs/`
2. Create `requirements.txt` with all libraries listed

**Candidate profile:**

3. Fill in `data/candidate_profile.json` completely — no `FILL_IN` fields remaining
4. Add `data/profile/master_resume.docx` — your base resume

**Skill dictionary:**

5. Review `data/skill_dictionary.json` — confirm the 10 categories and keywords reflect your target roles
6. Add any domain-specific terms you know appear in European HR/PM job postings that are not already covered
7. Note: the dictionary already exists (created during architecture phase) — this task is review and extension, not creation

**Role dictionary:**

8. Review `data/role_dictionary.json` — confirm the 8 categories and title variants cover the job titles you are targeting
9. Add any European-market title variants not already covered (e.g. "Stagiaire Produit", "Praktikant Strategy")
10. Note: the dictionary is created in this phase — see Phase 1 core infrastructure task below

**Architecture records:**

11. Review `docs/DECISIONS.md` — confirm all 7 ADRs are understood before Phase 1 code begins

**Prompt stubs:**

12. Create `prompts/scoring_prompt.txt` — stub (placeholder text, not yet written)
13. Create `prompts/company_research_prompt.txt` — stub
14. Create `prompts/resume_prompt.txt` — stub
15. Create `prompts/cover_letter_prompt.txt` — stub

These files exist from Phase 1 even though their content is written in later phases. The folder structure is always complete.

**Core infrastructure:**

16. Build `core/config.py` — score threshold, file paths, Claude model name, job board list, research quality thresholds, retry limits
17. Build `core/profile_loader.py` — loads and validates `candidate_profile.json`; raises a clear error if any required field is empty
18. Build `core/prompt_loader.py` — loads prompt `.txt` file by name; raises error if file is still a stub
19. Build `core/skill_loader.py` — loads `skill_dictionary.json`, returns a keyword→category lookup structure
20. Build `core/role_loader.py` — loads `role_dictionary.json`, classifies job title strings into role categories
21. Build `core/database.py` — creates all 6 tables (including updated `scores` table with `role_category`, `matched_categories`, `dictionary_version`, `role_dictionary_version`); basic read/write functions
22. Build `core/claude_client.py` — loads a prompt file + data, sends to Claude, returns response
23. Create `.env` with real API keys; create `.env.example` with placeholders
24. Verify `.gitignore` excludes `.env` and `outputs/`

**Entry point:**

25. Build `main.py` — show the numbered menu; do nothing else yet

### Phase 1 constraints check
- `grep -r "You are\|I am\|Score this" agents/` → zero results (no prompts in code)
- `grep -r "POLIMI\|Darpan" agents/` → zero results (no profile data in code)
- `data/candidate_profile.json` → fully populated, no `FILL_IN` values
- `data/skill_dictionary.json` → reviewed and confirmed; 10 categories populated
- `data/role_dictionary.json` → reviewed and confirmed; 8 categories populated
- All four `.txt` files exist in `prompts/`, even if they only contain `# STUB — TO BE WRITTEN`
- `core/skill_loader.py` → can load skill_dictionary.json and return a working lookup
- `core/role_loader.py` → can load role_dictionary.json and classify a job title string

### Deliverable
`python main.py` shows the numbered menu. The database file is created with all 6 empty tables. `profile_loader.load()` returns a valid profile object. `skill_loader.load()` returns a valid dictionary. Claude responds to a test ping via `claude_client.py`.

---

## Phase 2: Job Search
**Goal:** The agent finds real jobs, saves them to the database, and writes the initial `jobs_master.xlsx`.

### Tasks
1. Sign up for Apify — get API key, add to `.env`
2. Build `agents/search_agent.py`:
   - Load target roles and geography from `profile_loader.load()` — no hardcoded search terms
   - Search LinkedIn Jobs via Apify
   - Search Indeed Europe via Apify
   - Search InfoJobs (Italy) and StepStone (Europe) via Apify
   - Parse results into `jobs` table format
   - Deduplicate: skip jobs already in database (match on company + title + location)
3. Log each search run to the `searches` table
4. Build initial `jobs_master.xlsx` — one row per job, columns: title, company, location, source, URL, date found
5. Wire search into `main.py` menu option 1

### Phase 2 constraints check
- `search_agent.py` loads search terms from `profile_loader.load()` — not hardcoded strings

### Deliverable
Menu option 1 runs, finds 20+ real jobs, saves them to the database, and writes `outputs/exports/jobs_master_[date].xlsx`.

---

## Phase 3: Scoring + Company Research
**Goal:** Every job gets a score. Every approved job gets a research dossier. `jobs_master.xlsx` updates with scores and color coding.

### Scoring tasks
1. **Write `prompts/scoring_prompt.txt`** — full content:
   - Define the four scoring dimensions with exact point weights
   - Explain what each dimension means
   - Instruct Claude to receive pre-mapped skill categories (from skill_dictionary.json), not raw job text
   - Specify output format: total score, four sub-scores, matched_categories list, explanation
   - Add guidance for edge cases (adjacent roles, international location, career changers)
   - Test by pasting into Claude.ai with a sample pre-mapped skill analysis + profile before wiring into code

2. Build `agents/scoring_agent.py` using the five-step pipeline:
   - **Step A:** Load job title from database; call `role_loader.classify_title()` → get `role_category`
   - **Step B:** Load job description from database; extract skill keywords
   - **Step C:** Load `skill_dictionary.json` via `skill_loader.match_keywords()`; map extracted terms to skill categories
   - **Step D:** Load candidate profile via `profile_loader.load()`; compute role-weighted category overlap (direct match, adjacent match, no match)
   - **Step E:** Call `claude_client.py` with `scoring_prompt.txt` + structured analysis (role_category + skill overlap) + profile
   - Parse four sub-scores, `role_category`, `matched_categories` list, and explanation from Claude's response
   - Save to `scores` table with `prompt_version`, `dictionary_version`, `role_dictionary_version`, `role_category`, and `matched_categories` recorded
   - Add `--dry-run` mode: print score + matched categories without saving (for iterating on prompt or dictionary)

3. Update `jobs_master.xlsx` with score columns and color coding:
   - Green background: total score ≥ 70
   - Yellow background: total score 50–69
   - Red background: total score < 50

4. Wire score into `main.py` menu option 2

### Company Research tasks
5. **Write `prompts/company_research_prompt.txt`** — full content:
   - Define what to research: mission, recent news, culture, key people, talking points
   - Instruct Claude to self-assess confidence and return a research_quality score 0–10
   - Specify quality criteria: what justifies a 7+, what constitutes a 0–3
   - Specify output format (structured sections: mission, news, culture, key_people, talking_points, quality_score, quality_rationale)
   - Include instruction: if the company is small/obscure and little is verifiable, say so and assign a low score

6. Build `agents/company_research_agent.py`:
   - Load jobs above the score threshold from the database
   - For each: call `claude_client.py` with `company_research_prompt.txt` + company name + job description
   - Parse structured research response
   - Derive `research_quality_tier` from `research_quality` score:
     - 0–3 → "Poor" → `personalization_mode = "generic"`
     - 4–6 → "Moderate" → `personalization_mode = "company_aware"`
     - 7–10 → "Strong" → `personalization_mode = "deep_personalization"`
   - Save to `company_research` table with all three tier fields and `prompt_version` recorded
   - Print summary: "Nova Analytics — quality 2/10 (Poor): generic mode | Siemens — quality 9/10 (Strong): deep personalization"

7. Wire research into `main.py` menu option 3

### Phase 3 constraints check
- `scoring_agent.py`: loads prompt from `prompts/scoring_prompt.txt`; loads dictionary from `skill_loader.load()` — no prompt text or hardcoded skills in the file
- `company_research_agent.py`: loads prompt from `prompts/company_research_prompt.txt` — no prompt text in the file
- Both agents call `profile_loader.load()` — no hardcoded profile data
- All scored jobs have non-empty `matched_categories` in the `scores` table

### Deliverable
Menu option 2 scores all unscored jobs and shows matched skill categories. Menu option 3 researches approved companies and prints quality tier for each. `jobs_master.xlsx` shows scores with color coding. Menu option 5 shows all jobs ≥ 70 with score explanation and matched categories.

---

## Phase 4: Document Generation (LangGraph Pipeline)
**Goal:** A LangGraph pipeline generates a tailored resume and cover letter for each approved job, with a quality check that can retry if the output is too generic.

### Prompt writing (before any code)
1. **Write `prompts/resume_prompt.txt`** — full content:
   - Instructions to use ATS-friendly formatting and structure
   - How to naturally incorporate keywords from the job description
   - How to reference company research without being obvious
   - Output format that `document_builder.py` can parse into Word sections
   - Test by pasting into Claude.ai with a sample job + profile + research

2. **Write `prompts/cover_letter_prompt.txt`** — full content:
   - Tone and length guidelines (professional, specific, 3–4 paragraphs)
   - How to open with a company-specific hook from the research
   - How to connect experience to the role's specific requirements
   - Output format for `document_builder.py`
   - Test by pasting into Claude.ai

### LangGraph pipeline
3. Install LangGraph
4. Build `core/document_builder.py`:
   - Opens a Word template
   - Replaces named content sections with Claude's output
   - Preserves fonts, spacing, and layout from the template
   - Saves as `.docx` with the correct naming convention

5. Build `agents/resume_agent.py` as a LangGraph node:
   - Inputs: job, company research dossier (including `personalization_mode`), candidate profile
   - Loads `prompts/resume_prompt.txt` via `claude_client.py`
   - Passes `personalization_mode` to Claude as a context parameter:
     - `generic`: do not reference company specifics; use professional but neutral framing
     - `company_aware`: use confirmed company facts from dossier; no unverified specifics
     - `deep_personalization`: reference specific initiatives, news, and language from dossier
   - Passes output to `document_builder.py`

6. Build `agents/cover_letter_agent.py` as a LangGraph node:
   - Inputs: job, company research (including `personalization_mode`), profile, tailored resume
   - Loads `prompts/cover_letter_prompt.txt` via `claude_client.py`
   - Passes `personalization_mode` to Claude with same three-mode logic as Resume Agent
   - Passes output to `document_builder.py`

7. Build the Quality Check node:
   - Sends both documents back to Claude with a simple checklist prompt
   - Checks: Are keywords from the job description present? Is the company research referenced by name? Is the cover letter specific enough?
   - Returns PASS or FAIL with specific feedback

8. Build `agents/langgraph_pipeline.py`:
   - Wire nodes: Load Context → Resume → Cover Letter → Quality Check
   - Conditional edge from Quality Check: PASS → save | FAIL → retry (max 2 times)
   - On retry: pass the quality feedback back to Resume node as additional context
   - Save `langgraph_run_id` to `documents` table

9. Update `jobs_master.xlsx` with clickable document file links after generation

10. Wire generate into `main.py` menu option 4

### Phase 4 constraints check
- `resume_agent.py`: loads prompt from `prompts/resume_prompt.txt` — no prompt text in the file
- `cover_letter_agent.py`: loads prompt from `prompts/cover_letter_prompt.txt` — no prompt text in the file
- Both agents call `profile_loader.load()` — no hardcoded profile data
- Both agents receive `personalization_mode` from company research — no hardcoded personalization level
- `langgraph_pipeline.py`: contains only graph wiring logic — no prompts, no profile data, no quality logic

### Deliverable
Menu option 4 asks: "Generate documents for which job?" The LangGraph pipeline runs, quality-checks its own output, and saves both `.docx` files to `outputs/`. `jobs_master.xlsx` updates with clickable file links.

---

## Phase 5: Tracking + Excel Deliverables
**Goal:** A live application tracker and two production-ready Excel files.

### Tasks
1. Build `agents/tracker_agent.py`:
   - Update application status for any job
   - Add and edit notes and next-action reminders
   - Record contact name for each application

2. Build `core/excel_exporter.py` for `jobs_master.xlsx`:
   - Sheet 1: All jobs — every job ever found
   - Sheet 2: Top jobs — score ≥ 70, with research summary column
   - Color coding: green ≥ 70, yellow 50–69, red < 50
   - Clickable links to generated documents
   - Freeze top row; auto-filter on all columns

3. Build `core/excel_exporter.py` for `applications.xlsx`:
   - Sheet 1: Active applications (status is not Rejected or Withdrawn)
   - Sheet 2: Full history (every application ever)
   - Color coding by status: blue = Applied, yellow = Interview, green = Offer, red = Rejected
   - Next action date column highlighted red if within 3 days

4. Wire track and export into menu options 6, 7, 8

### Deliverable
Menu option 7 exports `outputs/exports/jobs_master_[date].xlsx`.
Menu option 8 exports `outputs/exports/applications_[date].xlsx`.
Both open cleanly in Excel and Google Sheets with no formatting errors.

---

## Phase 6: Orchestration + Polish
**Goal:** The full pipeline runs end-to-end with one menu selection.

### Tasks
1. Build `agents/orchestrator.py`:
   - Full pipeline: Search → Score → Research → Generate → Export
   - If one job fails, log the error and continue with the next
   - Print live progress: "Scoring job 12 of 50: AI Strategy Intern @ Siemens..."
   - Save a run log to `data/logs/run_[timestamp].txt`

2. Complete the interactive menu in `main.py`

3. Add weekly summary: jobs found, top scores, applications in progress, documents generated this week

4. Write `README.md`:
   - How to install Python
   - How to get Claude and Apify API keys
   - How to fill in `candidate_profile.json`
   - How to run `python main.py`

5. End-to-end test: run the full pipeline from zero, verify all outputs, confirm prompt files are loading correctly, confirm profile is loading from JSON

### Final constraints check (full project)
- `grep -rn "You are\|Score this\|Write a resume" agents/ core/` → zero results
- `grep -rn "POLIMI\|Darpan\|Milan" agents/ core/` → zero results
- All four prompt files have real content (not stubs)
- `data/candidate_profile.json` has no `FILL_IN` fields

### Deliverable
Menu option 9 runs the full pipeline. Terminal shows live progress. Ends with a summary and updated Excel files. All prompt files are plain text readable outside of code.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Prompt change breaks output format | Medium | Medium | Each prompt specifies exact output format; `claude_client.py` validates it before saving |
| Job board scraping gets blocked | Medium | High | Apify handles anti-bot; add rate limiting between requests |
| Company research returns low quality | Medium | Medium | Quality tier system gates personalization — Poor tier uses generic framing, no hallucination risk |
| LangGraph retry loop runs forever | Low | Medium | Hard cap of 2 retries per document; every retry is logged |
| candidate_profile.json has empty fields | High (early on) | Medium | `profile_loader.py` validates on load and stops with a clear error listing which fields are empty |
| Claude API costs too high | Low | Medium | Cache in database — never score or research the same job twice |
| Word document formatting breaks | Low | Medium | Locked template with named content zones; only those zones are replaced |

---

## API Cost Estimate

| Operation | Tokens per job | Cost per job | Cost for 50 jobs |
|---|---|---|---|
| Scoring | ~2,000 | ~$0.006 | ~$0.30 |
| Company research | ~2,500 | ~$0.008 | ~$0.40 |
| Resume generation | ~3,000 | ~$0.009 | ~$0.45 |
| Cover letter generation | ~2,000 | ~$0.006 | ~$0.30 |
| Quality check | ~1,500 | ~$0.005 | ~$0.25 |
| **Total per job** | | **~$0.034** | **~$1.70** |

A full weekly run costs approximately **$1.50–$2.50**. Cached results (already scored, already researched) cost nothing. Only new jobs consume credits.

---

## Approval Checkpoint

Before Phase 1 begins, confirm:

- [ ] Three architectural constraints understood: no hardcoded prompts, no hardcoded profile, skill dictionary must gate scoring
- [ ] All four prompt files will be created as stubs in Phase 1, filled in during Phases 3 and 4
- [ ] `data/candidate_profile.json` will be fully populated before any agent is built
- [ ] `data/skill_dictionary.json` has been reviewed and confirmed
- [ ] `data/role_dictionary.json` has been reviewed and confirmed — 8 role categories make sense for your target jobs
- [ ] `docs/DECISIONS.md` has been read — all 7 ADRs understood
- [ ] Six agents make sense: Search, Scoring, Company Research, Resume, Cover Letter, Tracker
- [ ] Research quality tiers (Poor/Moderate/Strong) and their effect on documents are understood
- [ ] LangGraph pipeline with quality-check loop is understood and wanted
- [ ] `jobs_master.xlsx` and `applications.xlsx` are the right deliverable format
- [ ] API cost model (~$1.50–$2.50 per weekly run) is acceptable
- [ ] Ready to start Phase 1

Once approved, Phase 1 begins.
