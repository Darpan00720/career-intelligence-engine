# Phase 2 Requirements — Job Search Agent

## Document Purpose

This document is the complete specification for the Phase 2 Job Search Agent. It defines every design decision, algorithm, data structure, source strategy, filtering rule, cost model, and risk that must be understood before a single line of code is written.

**Who this document is for:** The developer building `agents/search_agent.py`, and the product owner approving the design before implementation begins.

**What this document does not include:** Implementation code. No Python, no function signatures, no imports.

---

## Architecture Context

The Search Agent is Step 1 in the five-step pipeline:

```
Search Agent → Scoring Agent → Company Research → Document Pipeline → Tracker
(Phase 2)      (Phase 3)       (Phase 3)           (Phase 4)          (Phase 5)
```

The Search Agent's outputs are the inputs to everything that follows. Its data quality directly determines the quality of every resume, cover letter, and score the system produces. A corrupted job record produces a wrong score. A wrong score wastes research, resume generation, and application effort downstream.

**The Search Agent has exactly one job:** Find real job listings that match the candidate's profile, classify and filter them correctly, deduplicate them, and store them cleanly in the database.

**It does not call Claude.** No prompt file exists for the Search Agent. It is pure logic — no AI involved.

---

## Architectural Constraints That Apply to Phase 2

All four global constraints from the Implementation Plan apply:

1. **No hardcoded prompts** — not applicable to Search Agent (no Claude calls), but still enforced in any helper code.
2. **No hardcoded candidate data** — search terms, target roles, geography, and exclusion lists must all be loaded from `data/candidate_profile.json` via `profile_loader.load()`. No hardcoded strings like `"AI Product Manager Intern"` anywhere in the agent code.
3. **Skill dictionary must gate scoring** — not Phase 2's concern, but the jobs table must store `description` in full so Phase 3 can run the skill-matching pipeline.
4. **Role dictionary must gate title classification** — the Search Agent uses `role_loader.classify_title()` to pre-classify every job before storing it. This classification is stored in the `jobs` table so Phase 3 can verify it.

**New Phase 2 constraint — Search Agent must be profile-driven:**
`grep -r "AI Product Manager\|HR Analytics\|Milan\|London" agents/search_agent.py` must return zero results. Every search term, location, and preference comes from the profile JSON.

---

## 1. Search Sources

### Source Overview

There are two fundamentally different categories of job source. Understanding this distinction is the most important architectural insight in Phase 2.

| Category | Sources | How they work | Search method |
|---|---|---|---|
| **Public job boards** | LinkedIn, Indeed, Welcome to the Jungle | Aggregate jobs from many companies. Users search by keyword + location. | Keyword search via Apify scraper |
| **ATS company boards** | Greenhouse, Lever, Ashby, Workday, SmartRecruiters | Each company has its own branded job board hosted on the ATS provider's platform. No central search — each company's board is separate. | Company watchlist + direct API/scrape per company |

This distinction changes the architecture. You cannot search "all Greenhouse jobs for PM interns." Greenhouse is a platform used by thousands of companies, each with their own board at a unique URL. To use Greenhouse as a source, you must know in advance which companies use Greenhouse, and then check each one's board individually.

---

### Source 1: LinkedIn Jobs

**Why it is the primary source:**
LinkedIn is the dominant professional network in Europe. The majority of internship and junior roles posted by technology companies, consulting firms, scale-ups, and large enterprises in the target role tracks (Product Management, AI Strategy, HR Analytics) appear on LinkedIn first — often exclusively. Coverage is strong across all eight of the candidate's preferred cities.

**European coverage:**
LinkedIn has consistent job listing coverage for: Milan, London, Amsterdam, Berlin, Paris, Barcelona, Dublin, Zurich — all of the candidate's preferred locations.

**Access method:** Apify LinkedIn Jobs Scraper actor. LinkedIn has strong anti-scraping protections that make direct scraping unreliable. Apify maintains a managed actor with residential proxy rotation that bypasses these reliably. Direct `requests`-based scraping of LinkedIn is not viable.

**Key input parameters:**
- `searchKeywords` — search query string (e.g. "AI Product Manager Intern")
- `location` — city or country (e.g. "Milan, Italy", "Europe")
- `dateSincePosted` — "past week" (used on every run to fetch only new listings)
- `contractType` — "fulltime" or "internship" (run both separately per query)
- `experienceLevel` — "internship", "entry_level", "associate" (run separately)
- `limit` — maximum results per run (start at 50, scale to 100)

**Known limitations:**
- Job descriptions are sometimes truncated in LinkedIn's API response. The full description requires a second page load.
- Posted dates are sometimes approximate ("3 days ago") rather than exact dates. Parse these into relative offsets from the fetched date.
- Some listings are "promoted" (paid placement) and may be less relevant — store the `is_promoted` flag in `raw_data` if available.

---

### Source 2: Indeed (Country-Specific Domains)

**Why it is included:**
Indeed has strong European presence with country-specific job boards that surface local listings not always visible on LinkedIn. Indeed.it covers the Italian market well and surfaces Italian company postings that are often not listed on LinkedIn.

**Coverage by domain:**
| Domain | Market | Priority for this candidate |
|---|---|---|
| indeed.com | Global / US-first | Low — mostly US listings |
| indeed.co.uk | United Kingdom | High — strong London coverage |
| indeed.it | Italy | High — Milan and remote Italian listings |
| indeed.de | Germany | Medium — Berlin coverage |
| indeed.fr | France | Medium — Paris coverage |
| indeed.nl | Netherlands | Medium — Amsterdam coverage |
| indeed.es | Spain | Medium — Barcelona coverage |

Run separate Apify actor instances per domain. Results can overlap significantly with LinkedIn — deduplication handles this.

**Access method:** Apify Indeed Scraper. Each actor run targets one country domain, one search query, one location.

**Key input parameters:**
- `keyword` — search string
- `location` — city or region
- `country` — country code to determine which `indeed.[tld]` to search
- `maxItems` — cap per run
- `datePosted` — "last 7 days"

---

### Source 3: Welcome to the Jungle (WTTJ)

**Why it is included:**
Welcome to the Jungle is a European-first job board with particularly strong startup and scale-up coverage. It is widely used in France (its home market) and has significant traction in Italy, Spain, Germany, and the Netherlands. It is the best single source for Track A roles (Product Manager, AI Strategy) at European-born tech companies that are growing but not yet posting on every major board.

WTTJ also has strong English-language listings — European startups use it specifically because it presents companies as cultures, not just job listings. This makes it highly relevant for a candidate emphasizing a modern, AI-forward profile.

**Coverage:** Particularly strong for: Milan (Italy market growing), Paris (strongest market), Berlin, Amsterdam, Barcelona. Weaker for: London (LinkedIn dominates UK).

**Access method:** Apify generic web scraper or a community WTTJ actor. WTTJ's URL structure is predictable: `https://www.welcometothejungle.com/jobs?query=[keyword]&aroundQuery=[city]`. Use a JavaScript-capable scraper (not Cheerio) as WTTJ renders content client-side.

**Filter parameters available in URL:**
- `query` — search term
- `aroundQuery` — location
- `refinementList[contract_type]` — "INTERNSHIP", "FULL_TIME"
- `page` — pagination

---

### Sources 4–8: ATS Company Boards

**The company watchlist approach:**

These platforms (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) are Applicant Tracking Systems, not job boards. Each company using one of these platforms gets its own public job listing page at a predictable URL. You do not search them by keyword globally — instead, you maintain a watchlist of target companies and check each one's board on every search run.

**Why this approach is valuable:**
Many of the candidate's highest-priority target employers (large consultancies, scale-ups, HR tech companies, European AI companies) post exclusively on their ATS board and do not syndicate to LinkedIn or Indeed. Missing these means missing some of the best opportunities.

**The new data file: `data/company_watchlist.json`**

This file is the canonical list of companies to monitor via ATS boards. It is maintained by the user — add a company, specify which ATS it uses, provide its board identifier, and the agent automatically checks it on every run.

Structure:
```
{
  "version": "1.0",
  "last_updated": "2026-06-09",
  "companies": [
    {
      "name": "Zalando",
      "ats_provider": "greenhouse",
      "ats_id": "zalando",
      "board_url": "https://boards.greenhouse.io/zalando",
      "api_url": "https://boards-api.greenhouse.io/v1/boards/zalando/jobs?content=true",
      "industry": "E-commerce / Tech",
      "hq": "Berlin, Germany",
      "size": "Large Enterprise",
      "track_relevance": ["product_management", "ai_strategy"],
      "priority": "high",
      "notes": "Strong PM culture, AI roadmap announced 2025"
    }
  ]
}
```

**ATS-specific access methods:**

**Greenhouse** — Official public API (no scraping, no Apify needed):
```
https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs?content=true
```
Returns full JSON with all open roles, department, location, full description, and application URL. Free, rate-limit-tolerant, no authentication required. This is the most reliable data source in the entire system.

**Lever** — Official public API (no scraping, no Apify needed):
```
https://api.lever.co/v0/postings/{company_name}?mode=json
```
Returns JSON list of all open postings with title, team, location, description, and apply URL. Free, no authentication.

**Ashby** — Official public API (no scraping needed):
```
POST https://api.ashbyhq.com/posting-public.listActive
Body: {"organizationHostedJobsPageName": "{company_slug}"}
```
Returns structured JSON. Free, no authentication.

**Workday** — No public API. JavaScript-heavy application that requires Apify's JavaScript-capable scraper. Add companies via Apify's generic web scraper configured for Workday's known URL pattern: `{company}.wd{n}.myworkdayjobs.com/en-US/{career_page}`.

**SmartRecruiters** — Unofficial public endpoint:
```
https://api.smartrecruiters.com/v1/companies/{company_id}/postings?offset=0&limit=100
```
Not officially documented but stable. Returns JSON. Add Apify as fallback for companies where this endpoint fails.

---

### Source 9: Company Career Pages (Direct)

For companies that maintain a custom career page (not using a standard ATS), add the career page URL directly to `company_watchlist.json` with `"ats_provider": "custom"`. The Search Agent uses Apify's generic web scraper for these. Coverage is lower quality since custom career pages have inconsistent structure, but it ensures no target company is missed.

---

### Source 10: European Startup Job Boards

Secondary sources checked less frequently (bi-weekly rather than weekly):

| Board | URL | Best coverage | Priority |
|---|---|---|---|
| Talent.io | talent.io | France, Germany, Spain — senior tech | Low (too senior) |
| Otta | otta.com | UK, Europe — fast-growing startups | Medium |
| Wellfound (Angel.co) | wellfound.com | Global startups, equity roles | Medium |
| EU-Startups Jobs | eu-startups.com/jobs | European early-stage | Low volume |
| Honeypot | honeypot.io | Germany, Netherlands — tech-first | Low (developer-focused) |
| InfoJobs | infojobs.net | Italy, Spain | Medium for Track B |
| StepStone | stepstone.de | Germany, Belgium, Netherlands | Medium |

These boards are lower priority because they have less volume in the candidate's target tracks and significant overlap with LinkedIn. Run them bi-weekly; store results with a `job_board` tag so they can be filtered in `jobs_master.xlsx`.

---

## 2. Europe Filtering Strategy

### The Problem

Europe is not one job market. It is 30+ distinct markets with different languages, hiring norms, visa requirements, and job board ecosystems. A search for "Product Manager Intern" returns listings from Milan, London, Berlin, Paris, Warsaw, Tallinn — with wildly different contexts. The filtering strategy must select for the candidate's actual target geography without being so restrictive it misses remote roles or listings where a city name is not explicit.

### Geographic Filter Layers

**Layer 1: Job board domain**
Using country-specific Indeed domains (indeed.it, indeed.co.uk, indeed.de, etc.) provides the first coarse geographic filter for free.

**Layer 2: Location field matching**
Every job returned by Apify has a `location` field. Normalize and match against the candidate's preferred locations plus any European location when `open_to_all_europe = true`.

Normalization rules for the `location` field:
- Strip leading/trailing whitespace
- Map known variants: "Milano" → "Milan", "Londra" → "London", "Mailand" → "Milan"
- Treat "Remote", "Home Office", "Anywhere in Europe", "Fully Remote" as location-positive
- Treat locations outside Europe (US, India, APAC) as location-negative unless explicitly remote

**Layer 3: Description-level location mentions**
Some listings have `location = "Europe"` or `location = null` with the actual city buried in the job description. Extract city names from the description text as a fallback when the structured location field is empty or too vague.

**Layer 4: Visa and work permit language**
European target market jobs often state:
- "EU work permit required" or "right to work in the EU" — store this flag; do not exclude (candidate can assess)
- "Fluent German/French required" — store as a `language_requirement` flag; do not auto-exclude but flag for manual review
- "Based in [city] only, no relocation" — note in `raw_data`

**Layer 5: Seniority-filtered geography**
For Track B roles (HR Analytics, Workforce Planning) in Italian companies, Italian-language postings are acceptable because the candidate has professional Italian. For Track A roles (PM, AI Strategy) at tech companies, Italian-only postings are typically not appropriate unless the candidate applies in Italian — see Language Filtering below.

### Accepted Locations

Accept any job in:
1. The 8 preferred cities: Milan, London, Amsterdam, Berlin, Paris, Barcelona, Dublin, Zurich
2. Anywhere in Europe, if `remote_ok = true` and the job is flagged as remote or hybrid
3. Any European city, if `open_to_all_europe = true` and `relocation_ok = true`

Reject any job in:
- United States, Canada, LATAM, APAC, MENA — unless explicitly marked "open to Europe remote"
- The candidate's `exclude_locations` list (currently empty — extensible)

---

## 3. English-Language Filtering

### The Business Rule

Target Track A (Product Management, AI Strategy, Business Strategy) — **English only.**
Target Track B (HR Analytics, People Analytics, Workforce Planning) — **English preferred; Italian accepted.**

**Why this distinction:** PM and AI Strategy internships at European tech companies are almost universally conducted in English — interviews, deliverables, team communication. A job posting exclusively in Italian for these roles typically signals a local Italian company requiring native Italian, which is not the candidate's primary positioning. HR Analytics and People Analytics roles at Italian or international companies operating in Italy may be in Italian even when the actual work is partially in English; the candidate's Italian level makes these acceptable.

### Language Detection Method

**Step 1: Check the `language` metadata field**
Some Apify actors return a `language` field directly. Use it if present.

**Step 2: Detect from title**
If the job title is in French, German, Spanish, Italian, or Portuguese with no English equivalent, the posting is likely in that language. A title like "Responsabile Analytics" (Italian) or "Stagiaire Product Manager" (French with English term) signals language.

**Step 3: Detect from description**
Use Python's `langdetect` library (already installable, no additional API) on the first 500 characters of the description. `langdetect` handles European languages well and is fast. Store the detected language code (`en`, `it`, `fr`, `de`, etc.) in the `raw_data` JSON blob.

**Note:** `langdetect` is probabilistic, not deterministic. For short descriptions it can be wrong. The recommended handling is:
- `confidence > 0.95` and `language = en` → accept
- `confidence > 0.95` and `language = it` → apply Track B rule (accept for Track B, reject for Track A)
- `confidence < 0.95` → store detected language, do not auto-reject; flag for manual review

**Step 4: Handle bilingual postings**
Some European postings are half in English, half in Italian or French. These often have English title + local-language requirements section. These are acceptable — if the title is English and `langdetect` detects the description as mixed, classify as English.

### What to Do with Non-English Postings

Do not delete or permanently reject them. Store them in the database with a `language` flag in `raw_data`. The scoring agent can still attempt to score them (Claude handles multiple languages). The user can review them manually if desired.

---

## 4. Internship and Seniority Filtering

### Business Context

The candidate is targeting:
- Track A: explicitly internship-level roles (Intern, Stage, Tirocinio)
- Track B: junior/associate full-time roles AND internships

The `seniority_preference` in the profile is `["Intern", "Junior", "Associate"]`. This maps to:

### Seniority Accept Rules

Accept a job if the title or description contains any of:
- `intern`, `internship`, `stage`, `stagista`, `stagaire`, `praktikum`, `praktikant`, `tirocinio`, `tirocinante`
- `junior`, `jr.`, `entry level`, `entry-level`, `graduate`, `graduate programme`, `grad scheme`
- `associate` (used by consulting firms — "Associate Consultant" is typically 0-2 years)
- `0-2 years experience`, `0-1 year`, `no experience required`, `recent graduate`
- `fellowship`, `rotational programme`, `management trainee`, `analyst` (common Track B entry title)

### Seniority Reject Rules

Reject a job if the title contains any of (exact word boundary match, not substring):
- `senior`, `sr.`, `lead` (except "team lead" at small companies — ambiguous, keep)
- `manager` (except "associate manager", "junior manager", "product manager" which is an entry-level PM title)
- `director`, `head of`, `chief`, `vp`, `vice president`, `c-level`, `cxo`
- `principal`, `staff`, `distinguished`
- `partner` (in consulting context)
- `5+ years`, `7+ years`, `10+ years` in the requirements body

**Edge case — "Product Manager":**
"Product Manager" without "Senior" or "Junior" is an entry-level title at most companies. Accept it. Apply the seniority scoring sub-dimension in Phase 3 to let Claude determine whether the role is actually entry-level based on description content.

**Edge case — "Manager" in Track B:**
"HR Analytics Manager" or "People Analytics Manager" is typically mid-level. Flag but do not auto-reject — let the Scoring Agent's seniority_fit sub-score handle it. Better to over-include and score accurately than to under-include and miss opportunities.

### Contract Type Accept Rules

Accept: `full-time`, `internship`, `fixed-term`, `contract`, `part-time` (only if remote and internship)
Reject: `freelance`, `contractor` (B2B)

---

## 5. Job Classification Framework

### Overview

Every job fetched from any source must be classified before it is stored. Classification serves two purposes:
1. **Acceptance gate:** Decide whether to store the job at all (reject excluded categories)
2. **Track assignment:** Assign Track A or Track B to every stored job

Classification uses the existing `role_loader.classify_title()` function. No new classification code is needed — only the decision logic around what to accept, reject, and flag.

### Track Assignment Logic

```
role_category → track assignment

product_management  → Track A
ai_strategy         → Track A
business_strategy   → Track A

people_analytics    → Track B
hr_analytics        → Track B
workforce_planning  → Track B
talent_acquisition  → Track B (with exclusion filter — see below)
digital_transformation → Track A or Track B (ambiguous — flag for review)
unknown             → Unclassified (store but flag)
```

`digital_transformation` sits on the boundary between both tracks. A "Digital Transformation Intern" at a consulting firm is Track A. A "Digital HR Transformation Analyst" is Track B. Store it as unclassified but above-threshold and let the Scoring Agent determine the actual fit.

### Excluded Role Categories

Titles that should never be stored, regardless of score:

**Excluded by title keyword (exact word boundary):**
- `hr generalist`, `hr operations`, `hr admin`, `hr administrator`
- `payroll`, `payroll specialist`, `payroll analyst`, `payroll coordinator`
- `recruitment coordinator`, `recruiter` (unless modified by "strategic", "technical", "executive", or "talent intelligence")
- `hr coordinator`, `hr assistant`, `hr support`
- `benefits`, `compensation` (standalone — "Total Rewards" roles excluded unless Analytics is in title)
- `learning and development` coordinator (L&D Analyst or L&D Strategy is acceptable)

**Why talent_acquisition is included but recruitment coordinator is excluded:**
The user's track_2 includes "Talent Acquisition" as a target. Senior and strategic TA roles (Talent Acquisition Manager, Head of TA, Talent Intelligence Analyst) are good fits. Operational TA roles (Recruitment Coordinator, Recruiter with no strategy component) are not fits. The exclusion list handles this nuance.

**Implementation:**
The exclusion list should be maintained in `data/candidate_profile.json` under `application_preferences.exclude_title_keywords`. This allows the user to update it without code changes. Current exclusion keywords:
```json
"exclude_title_keywords": [
    "hr generalist", "hr operations", "hr admin", "hr administrator",
    "payroll", "recruitment coordinator", "hr coordinator", "hr assistant",
    "hr support", "compensation specialist", "benefits specialist"
]
```

The agent checks each fetched job title against this list using the same word-boundary matching applied in the skill loader.

### Classification Decision Tree

```
Job fetched from any source
         │
         ▼
Does title contain an excluded keyword?
    YES → Reject entirely (do not store)
    NO  ↓
         ▼
classify_title(title) → role_category
         │
         ▼
role_category == "unknown"?
    YES → Store with track = "unclassified", flag for manual review
    NO  ↓
         ▼
role_category in Track A list?
    YES → Store with track = "A"
    NO  ↓
role_category in Track B list?
    YES → Store with track = "B"
    NO  → Store with track = "unclassified", flag for manual review
```

### Schema Addition Required Before Phase 2

Two new columns must be added to the `jobs` table before the Search Agent can store these classification results:

| New column | Type | Description |
|---|---|---|
| `track` | TEXT | "A", "B", or "unclassified" |
| `language` | TEXT | Detected language code: "en", "it", "fr", "de", etc. |
| `dedup_hash` | TEXT | Content fingerprint for cross-board deduplication (see Section 6) |
| `role_category` | TEXT | result of role_loader.classify_title() run at fetch time |
| `is_excluded` | BOOLEAN | True if matched exclusion list (should not occur in stored rows, but useful for audit) |

Note: `role_category` in the `jobs` table is a pre-classification that the Scoring Agent can later override with a more nuanced classification. The two values may differ if the Scoring Agent uses context from the description, not just the title.

---

## 6. Deduplication Strategy

### The Problem

The same job listing appears in multiple places:
- A Siemens AI Strategy internship is posted on LinkedIn, appears in Indeed Germany, is also listed on Siemens' Greenhouse board, and is syndicated to Welcome to the Jungle.
- Every week LinkedIn re-promotes some old listings at the top of search results, giving them a newer appearance.
- A company posts the same role in two locations (Milan and Remote) as separate listings with 95% identical descriptions.

Without deduplication, the candidate sees the same job four times in their spreadsheet, wastes time reviewing duplicates, and risks applying to the same position twice.

### Three-Tier Deduplication

**Tier 1: URL exact match** (already implemented)
The `jobs` table has a `UNIQUE` constraint on the `url` column. `INSERT OR IGNORE` silently skips any job whose URL is already in the database. This is the cheapest and most reliable deduplication method. It catches: the same job re-fetched from the same board on subsequent runs.

**Tier 2: Content fingerprint** (new — Phase 2)
The same job appears on multiple boards at different URLs. Tier 1 misses this.

Algorithm:
```
dedup_hash = SHA-256(
    normalize(company_name) +
    normalize(job_title) +
    location_country_code
)
```

Where `normalize()` means: lowercase, strip punctuation, collapse whitespace, strip common suffixes ("GmbH", "S.p.A.", "Ltd", "Inc", "SE").

Add `dedup_hash` as a `UNIQUE` column in the `jobs` table. When a new job's `dedup_hash` matches an existing row, `INSERT OR IGNORE` skips it. Record the duplicate source in a log so the user can see coverage across boards.

**Why include country code but not city?**
The same role can be listed as "Milan" on one board and "Italy" on another. City normalization is error-prone. Country code is reliably available and good enough for fingerprinting.

**Why not include the description in the hash?**
Descriptions vary slightly across boards (HTML stripping, character encoding, truncation). Including description makes the hash too sensitive — the same job would hash differently across boards.

**Tier 3: Title/Company fuzzy window** (Phase 6, not Phase 2)
Detect probable duplicates where the same company posts a very similar role twice within 14 days (e.g., "AI PM Intern" and "AI Product Manager Intern"). Levenshtein distance on the normalized title, windowed by company and date. Flag these for user review rather than auto-rejecting. Complexity is too high for Phase 2.

### Database Change Required

Add `dedup_hash TEXT UNIQUE` column to the `jobs` table. Since the database is still in development (no live data), this is a schema migration: drop and recreate the table with the new column, or add via `ALTER TABLE jobs ADD COLUMN dedup_hash TEXT`. Note: SQLite does not support `ADD COLUMN ... UNIQUE` — add the column first, then add a UNIQUE index via `CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup_hash ON jobs(dedup_hash)`.

### Handling Duplicates at Storage Time

When a job is rejected by either Tier 1 or Tier 2:
- Do not raise an error — this is expected behavior
- Retrieve the existing job's ID from the database (using `get_job_by_url()` or `get_job_by_hash()`)
- Log: "Duplicate skipped: [title] @ [company] — already stored as job_id=[id] from [original_source]"
- Return the existing job_id to the calling code so it can proceed to scoring if needed

This requires the `get_job_by_url()` function noted in the Phase 1 audit. It must be built before Phase 2.

---

## 7. Search Workflow

### Two Operating Modes

The Search Agent operates in two modes per run. Both modes run on every search run (menu option 1).

**Mode A: Discovery Search**
Searches public job boards by keyword. Finds new jobs from companies the agent has never seen before. High volume, lower precision.

Sources: LinkedIn, Indeed (per country domain), Welcome to the Jungle, secondary startup boards.
Frequency: Every run (weekly or as needed).
Expected output per run: 30–100 results before filtering.

**Mode B: Company Watch**
Checks every company in `company_watchlist.json` and fetches their current open positions via ATS API. Finds jobs at known high-priority companies that may not appear in keyword searches. Lower volume, higher precision.

Sources: Greenhouse API, Lever API, Ashby API, Workday/SmartRecruiters via Apify.
Frequency: Every run.
Expected output per run: 5–30 results before filtering.

### Step-by-Step Workflow

```
STEP 1: LOAD PROFILE
   profile = profile_loader.load()
   Extract: target_roles, target_geography, application_preferences
   Build: search queries, exclusion list, location list

STEP 2: BUILD SEARCH QUERY MATRIX
   For each role_keyword in (Track A keywords ∪ Track B keywords):
     For each location in preferred_locations:
       For each contract_type in contract_types:
         Create query = {keyword, location, contract_type}
   Deduplicate queries (same keyword + location = one query)
   Result: query_matrix = list of (keyword, location, contract_type) tuples

STEP 3: DISCOVERY SEARCH — Mode A
   For each source in [linkedin, indeed_per_domain, wttj]:
     For each query in query_matrix:
       raw_results = apify_client.run(actor, query_params)
       For each result in raw_results:
         Apply filters (exclusion, seniority, language, geography)
         If accepted:
           classify(result)
           deduplicate(result)
           if not duplicate:
             insert_job(result)
             log_accepted()
           else:
             log_duplicate()
         else:
           log_rejected(reason)
   Log search run to searches table

STEP 4: COMPANY WATCH — Mode B
   watchlist = load_company_watchlist()
   For each company in watchlist:
     raw_listings = fetch_ats_listings(company)  # via API or Apify
     For each listing in raw_listings:
       Apply filters (exclusion, seniority, language)
       If accepted:
         classify(listing)
         deduplicate(listing)
         if not duplicate:
           insert_job(listing)
   Log search run to searches table

STEP 5: GENERATE SUMMARY REPORT
   Print to terminal:
     - Jobs found this run (total)
     - Jobs accepted (stored)
     - Jobs rejected (by category: excluded, wrong language, wrong seniority, wrong location)
     - Duplicates skipped
     - New jobs by track (A / B / unclassified)
     - New jobs by source (LinkedIn / Indeed / WTTJ / company watch)
     - Total jobs in database
     - Jobs awaiting scoring

STEP 6: CREATE OR UPDATE INITIAL JOBS_MASTER.XLSX
   Export all unscored jobs (no score row yet) to jobs_master.xlsx
   This is a lightweight export — only the columns available without scoring:
   ID, Track, Title, Company, Location, Source, Role Category, URL, Posted Date, Fetched Date, Language
```

### Search Query Construction

Track A keywords (from `target_roles.track_1_product_and_strategy`):
```
"AI Product Manager Intern"
"AI PM Intern"
"Product Manager Intern"
"Product Management Intern"
"AI Strategy Intern"
"Business Strategy Intern"
"AI Strategy Analyst"
"Product Strategy Intern"
```

Track B keywords (from `target_roles.track_2_strategic_hr`):
```
"HR Analytics Analyst"
"People Analytics Analyst"
"Workforce Planning Analyst"
"Talent Intelligence Analyst"
"HR Data Analyst"
"People Data Analyst"
"Strategic HR Analyst"
"HR Analytics Intern"
"People Analytics Intern"
"Workforce Planning Intern"
```

European variants to add (multilingual awareness):
```
"Stagiaire Product Manager"       (French)
"Analyste RH"                     (French)
"Praktikant Produktmanager"       (German)
"HR Analytics Praktikant"         (German)
"Analista HR"                     (Italian/Spanish)
"Stage AI Strategy"               (Italian/French)
```

**Important:** These must not be hardcoded in the agent. They should be loaded from the profile's `target_roles` field and supplemented by a `search_keywords` extension field in the profile (or derived algorithmically from the role dictionary titles).

Recommended: Add `"search_keyword_variants"` to `candidate_profile.json` under `target_roles` — an optional list of additional search strings the user wants included. This keeps the agent profile-driven while allowing custom keyword additions without code changes.

---

## 8. `jobs_master.xlsx` Structure

### Design Philosophy

`jobs_master.xlsx` is the user's primary working document. It must be readable, filterable, actionable, and beautiful in both Excel and Google Sheets. It contains everything the agent has found and done — from raw job listing to application outcome.

The file is not a raw database dump. It is a curated, formatted, multi-sheet report.

### Sheet Structure (4 Sheets)

---

**Sheet 1: "All Jobs"**
Every job ever found. Ordered by score descending, then by fetched_date descending.

| Column | Source | Notes |
|---|---|---|
| ID | `jobs.id` | Hidden or narrow; for reference |
| Track | derived from `jobs.track` | "A" or "B" or "?" |
| Title | `jobs.title` | |
| Company | `jobs.company` | |
| Location | `jobs.location` | |
| Language | `jobs.language` | "EN", "IT", "FR", etc. |
| Role Category | `jobs.role_category` | From role_dictionary |
| Source | `jobs.job_board` | LinkedIn, Indeed, Greenhouse, etc. |
| Score | `scores.total_score` | Blank if not yet scored |
| Role Fit | `scores.role_fit` | Sub-score, 0–30 |
| Skills Match | `scores.skills_match` | Sub-score, 0–40 |
| Location Fit | `scores.location_fit` | Sub-score, 0–15 |
| Seniority Fit | `scores.seniority_fit` | Sub-score, 0–15 |
| Matched Skills | `scores.matched_categories` | Comma-separated skill categories |
| Explanation | `scores.explanation` | Claude's plain-English rationale |
| Status | `applications.status` | Found / Shortlisted / Applied / Interview / Offer / Rejected |
| Applied Date | `applications.applied_date` | |
| Notes | `applications.notes` | |
| Next Action | `applications.next_action` | |
| Next Action Date | `applications.next_action_date` | |
| Resume | `documents.file_path` (type=resume) | Clickable hyperlink to .docx file |
| Cover Letter | `documents.file_path` (type=cover_letter) | Clickable hyperlink to .docx file |
| Posted Date | `jobs.posted_date` | |
| Fetched Date | `jobs.fetched_date` | |
| URL | `jobs.url` | Clickable hyperlink to original posting |

**Color coding (row-level, based on score):**
- Score ≥ 70: light green row fill
- Score 50–69: light yellow row fill
- Score < 50: light red/pink row fill
- No score yet: white / no fill

**Cell-level color coding (status column):**
- "Found": grey
- "Shortlisted": light blue
- "Applied": medium blue
- "Interview": orange
- "Offer": gold
- "Rejected": light red
- "Withdrawn": light grey

**Formatting rules:**
- Freeze top row and leftmost 4 columns (ID, Track, Title, Company)
- Auto-filter on all columns
- Wrap text off (single-line rows for readability)
- Column widths: auto-sized on export, with max width 60 for explanation

---

**Sheet 2: "Shortlisted"**
Jobs with score ≥ 70 that have not yet been applied to. Sorted by score descending.

Same columns as Sheet 1, minus the low-information columns (Language, Source, Role Category).
This is the user's daily working view — the 20–30 jobs they are actively reviewing.

---

**Sheet 3: "Applied"**
Jobs where `applications.status` is "Applied", "Interview", "Offer", "Rejected", or "Withdrawn".
Sorted by applied_date descending.

Additional column added:
- `Days Since Applied` — calculated column: `=TODAY()-applied_date`
- Highlight red if Days Since Applied > 21 and status is still "Applied" (no response in 3 weeks)

---

**Sheet 4: "Pipeline Summary"**
A dashboard view. Not raw data — derived summaries.

| Metric | Value |
|---|---|
| Total jobs found | COUNT(jobs) |
| Jobs scored | COUNT(scores) |
| Jobs ≥ 70 | COUNT(scores WHERE total_score ≥ 70) |
| Track A ≥ 70 | COUNT WHERE track = 'A' AND score ≥ 70 |
| Track B ≥ 70 | COUNT WHERE track = 'B' AND score ≥ 70 |
| Documents generated | COUNT(documents) |
| Applications submitted | COUNT(applications WHERE status != 'Found') |
| Active interviews | COUNT(applications WHERE status = 'Interview') |
| Offers | COUNT(applications WHERE status = 'Offer') |
| Run cost this month | Estimated from searches.jobs_found (see Cost section) |

---

## 9. API Strategy

### Decision: Apify for Public Boards, Native APIs for ATS

The fundamental API strategy is two-tier:

**Tier 1 — Public Job Board Scraping via Apify:**
LinkedIn, Indeed, Welcome to the Jungle, and custom career pages do not provide official public APIs for job search. Apify provides managed actor infrastructure with:
- Residential proxy rotation (prevents IP blocking)
- Maintained selectors (Apify updates actors when job boards change their HTML structure)
- Retry and error handling
- Structured JSON output
- Cost control (pay per result, set max limits)

**Tier 2 — Native ATS APIs (no Apify needed):**
Greenhouse, Lever, and Ashby expose official, undocumented-but-stable public APIs designed for developers to build career site integrations. These APIs:
- Return clean structured JSON (no parsing needed)
- Have no rate limits for reasonable usage
- Require no authentication
- Are maintained by the ATS vendor — will not break without notice
- Are completely free

Using native ATS APIs for Greenhouse/Lever/Ashby is strictly better than scraping or using Apify for these sources. It is cheaper, more reliable, and more complete (APIs return the full description, not a truncated version).

### Error Handling Strategy

Every API call must be wrapped in try/except. The Search Agent must not crash because one job board is unavailable.

**Per-source error handling:**
- If LinkedIn scrape fails: log error, skip LinkedIn this run, continue with other sources
- If a specific company's Greenhouse API returns 404: mark company as "inactive" in watchlist, continue
- If Apify actor run fails: log the actor name, run_id, and error message; do not retry automatically (Apify handles retries internally)

**Partial success reporting:**
After each run, the summary must report which sources succeeded and which failed. "LinkedIn: 45 results ✓ | Indeed.it: FAILED (timeout) | WTTJ: 12 results ✓" — so the user knows whether coverage was complete.

### Rate Limiting

The Search Agent should space requests to be a polite citizen of each platform, even when using Apify's proxy infrastructure:
- Between Apify actor runs: 2-second minimum pause
- Between ATS API calls (Greenhouse, Lever): 500ms minimum pause
- Do not run all actors in parallel (sequential is fine — runtime is not a concern for a personal tool)

---

## 10. Apify Strategy

### Account and Authentication

Access the Apify client using the `APIFY_API_KEY` from `.env`. The Python client is `apify-client` (already in `requirements.txt`).

Standard pattern for every actor call:
```
client = ApifyClient(os.environ["APIFY_API_KEY"])
run = client.actor("actor-name").call(run_input=params)
results = client.dataset(run["defaultDatasetId"]).iterate_items()
```

### Actors to Use Per Source

| Source | Actor | Notes |
|---|---|---|
| LinkedIn | `bebity/linkedin-jobs-scraper` | Most maintained LinkedIn actor; supports all relevant filters |
| Indeed | `misceres/indeed-scraper` | Supports multi-country; run per country domain |
| Welcome to the Jungle | `apify/web-scraper` with custom config | WTTJ may not have a dedicated actor — use generic JS scraper |
| Workday company boards | `apify/web-scraper` | Workday is JS-heavy; requires full browser rendering |
| SmartRecruiters | First try native API; `apify/web-scraper` as fallback | |
| Custom career pages | `apify/web-scraper` or `apify/cheerio-scraper` | Choose based on whether page uses JavaScript |

### Input Configuration Per Actor

**LinkedIn Actor (`bebity/linkedin-jobs-scraper`) input:**
```json
{
  "searchKeywords": "AI Product Manager Intern",
  "location": "Milan, Italy",
  "dateSincePosted": "past week",
  "contractType": "fulltime",
  "experienceLevel": "internship",
  "limit": 50,
  "proxy": {
    "useApifyProxy": true,
    "apifyProxyGroups": ["RESIDENTIAL"]
  }
}
```
Run once per (keyword, location, contract_type, experience_level) combination.

**Indeed Actor (`misceres/indeed-scraper`) input:**
```json
{
  "keyword": "HR Analytics Analyst",
  "location": "London",
  "country": "UK",
  "maxItems": 50,
  "datePosted": "last 7 days"
}
```

### Actor Output Mapping

Every Apify actor returns different field names. The Search Agent must normalize all outputs to the `jobs` table schema. Define a mapping function per actor:

**LinkedIn output → jobs schema:**
```
result["jobTitle"]     → jobs.title
result["companyName"]  → jobs.company
result["location"]     → jobs.location
"linkedin"             → jobs.job_board
result["jobUrl"]       → jobs.url
result["description"]  → jobs.description
result["postedAt"]     → jobs.posted_date (parse relative date)
json.dumps(result)     → jobs.raw_data
```

**Greenhouse API output → jobs schema:**
```
job["title"]                          → jobs.title
company_name (from watchlist)         → jobs.company
job["location"]["name"]               → jobs.location
"greenhouse"                          → jobs.job_board
job["absolute_url"]                   → jobs.url
job["content"]  (HTML — strip tags)   → jobs.description
job["updated_at"]                     → jobs.posted_date
json.dumps(job)                       → jobs.raw_data
```

Build one normalization function per source. Store the original in `raw_data` before normalization so nothing is lost.

### Apify Dataset Management

Each actor run creates a named dataset in the Apify cloud. These datasets persist for 7 days by default. To prevent accumulation of stale datasets, either:
1. Delete the dataset after reading results: `client.dataset(run_id).delete()`
2. Set a very short retention in actor settings

For debugging purposes, keep the dataset alive for 24 hours (do not delete immediately). This allows inspecting raw results if filtering or classification behaved unexpectedly.

---

## 11. LinkedIn Strategy

### Why LinkedIn is Treated Separately

LinkedIn deserves its own section because it is both the most important source (highest volume, widest company coverage) and the most technically complex (strongest anti-scraping measures, most nuanced filter options, highest cost per result).

### Search Query Strategy

Do not use a single broad search. Run multiple targeted searches per track:

**Track A searches:**
| Query | Location | Experience | Contract |
|---|---|---|---|
| "AI Product Manager Intern" | Milan | Internship | Any |
| "Product Manager Intern" | Milan | Internship | Any |
| "AI Strategy Intern" | London | Internship | Any |
| "Business Strategy Intern" | Amsterdam | Internship | Any |
| "AI Product Manager" | Europe | Entry-level | Any |
| "Product Strategy" | Remote | Entry-level | Any |

**Track B searches:**
| Query | Location | Experience | Contract |
|---|---|---|---|
| "HR Analytics" | Milan | Entry-level | Any |
| "People Analytics" | London | Entry-level | Any |
| "Workforce Planning Analyst" | Europe | Entry-level | Any |
| "Talent Intelligence" | Europe | Any | Any |
| "HR Analytics" | Remote | Any | Any |

Total: approximately 12–15 LinkedIn searches per weekly run. At 50 results each, this is 600–750 raw results before filtering.

### LinkedIn Date Filtering

LinkedIn returns relative dates ("3 days ago", "2 weeks ago"). Convert to absolute date by subtracting from `fetched_date`. On each run, filter for `dateSincePosted = "past week"` to avoid reprocessing old listings. The `dedup_hash` catches any that slip through.

### LinkedIn-Specific Limitations and Workarounds

**Description truncation:** LinkedIn's API returns a truncated description (approximately 500 characters). The full description requires a second request to the job detail page. The Apify actor handles this automatically when `fetchJobDetails: true` is set. Always enable this.

**"Easy Apply" vs "External Apply":** LinkedIn has two application types. "Easy Apply" = apply directly on LinkedIn. "External Apply" = redirects to company website/ATS. Store the application type in `raw_data`. Most high-quality roles use "External Apply" which leads to the company's actual ATS (often Greenhouse or Lever) — this is useful for the watchlist (confirm which ATS a company uses).

**Promoted listings:** LinkedIn surfaces paid placements prominently. These are not necessarily better jobs. Flag `is_promoted = true` in `raw_data` but do not filter them out — a promoted PM internship at Siemens is still worth scoring.

**Company size filter:** LinkedIn allows filtering by company size (1–10, 11–50, 51–200, 201–500, 501–1000, 1001–5000, 5001–10000, 10001+). The candidate's profile specifies `preferred_company_sizes: ["Startup", "Scale-up", "Large Enterprise"]`. Map these to LinkedIn size buckets and add as search parameters to reduce noise.

### Anti-Blocking Considerations

The Apify LinkedIn actor with residential proxies handles most blocking scenarios. Additional best practices:
- Do not run LinkedIn searches more than once per day
- Space actor runs: 5-minute gap between LinkedIn actor calls
- If an actor run returns 0 results unexpectedly, log it as a potential block; retry on next scheduled run (do not retry immediately)
- Monitor the `error` field in Apify run results; if `status = FAILED`, log the error message

---

## 12. Cost Estimates

### Apify Costs

Apify pricing is based on actor compute units consumed, not number of results. The following estimates are based on the `bebity/linkedin-jobs-scraper` and `misceres/indeed-scraper` actors at current Apify rates:

| Source | Results per run | Cost per 100 results | Weekly cost (1 run) | Monthly cost (4 runs) |
|---|---|---|---|---|
| LinkedIn (all queries) | ~600 | ~$0.80 | ~$4.80 | ~$19.20 |
| Indeed (per domain × 6) | ~300 | ~$0.40 | ~$1.20 | ~$4.80 |
| Welcome to the Jungle | ~50 | ~$0.30 | ~$0.15 | ~$0.60 |
| Workday/SmartRecruiters via Apify | ~20 | ~$0.50 | ~$0.10 | ~$0.40 |
| **Total Apify** | | | **~$6.25** | **~$25.00** |

Greenhouse, Lever, Ashby APIs: **$0.00** — free official APIs.

**Apify Free Tier Coverage:** The Apify free tier includes $5/month credit. Weekly runs are not sustainable on the free tier unless the search volume is reduced. At 2 runs/month (bi-weekly) instead of 4, the estimated Apify cost drops to ~$12.50/month — still above the free tier but well within the $49/month Starter plan.

**Optimization options to stay within free tier:**
1. Run LinkedIn searches for only the most specific queries (5 instead of 15) → reduces results to ~200 → cost ~$1.60/run × 2 runs = $3.20/month
2. Use Indeed only for Italy (indeed.it) and UK (indeed.co.uk) → drop 4 domains → saves ~$0.80/month
3. Run secondary startup boards monthly instead of weekly → saves ~$0.25/month
4. Net result: ~$2.50/month — within free tier

**Recommended approach:** Start with the reduced query set in the first 4 weeks while the system is being calibrated. Once scoring is working and quality is confirmed, scale to full query volume.

### Anthropic API Costs

The Search Agent does not call Claude. No Anthropic cost in Phase 2.

Anthropic costs begin in Phase 3 (Scoring Agent). Estimates are in the Implementation Plan: approximately $1.50–$2.50 per full weekly pipeline run on 50 jobs.

### Total Phase 2 Monthly Cost

| Component | Monthly cost |
|---|---|
| Apify (full volume) | ~$25.00 |
| Apify (reduced volume, first month) | ~$2.50 |
| Anthropic | $0.00 (Phase 2 only) |
| Everything else | $0.00 |

---

## 13. Scaling Strategy

### Current Scale: Personal Use (Phase 2)

- 1 candidate profile
- 5 primary job boards + company watchlist (~20 companies)
- 1–2 runs per week
- 15 applications maximum per week
- Expected accepted jobs per run: 10–30 (after filtering)

### Scaling Dimension 1: More Companies in Watchlist

The company watchlist is designed for incremental growth. Adding a company takes 5 minutes (find their ATS provider, add one JSON entry). The Greenhouse and Lever APIs scale with zero additional cost. Every company added to the watchlist increases precision (known target companies) without increasing Apify cost.

Target watchlist size by end of Phase 2: 30–50 companies.
Suggested company categories to add:
- Large consultancies with digital practices: Accenture, McKinsey, Bain, BCG, Deloitte, EY, KPMG, PwC
- European tech companies: Zalando, Spotify, Delivery Hero, Klarna, Trade Republic, N26, Revolut
- HR Tech companies: Workday, SAP SuccessFactors, HiBob, Personio, Beamery, Eightfold AI
- AI companies with European presence: Mistral AI, DeepMind, Aleph Alpha, MOSTLY AI
- Consulting firms with AI practices: Roland Berger, Oliver Wyman, A.T. Kearney

### Scaling Dimension 2: More Search Queries

The search query matrix grows with the role dictionary. Adding new role titles to `role_dictionary.json` automatically generates new search queries without code changes — as long as the agent derives queries from the profile and role dictionary, not from hardcoded strings.

### Scaling Dimension 3: Frequency

Once the system is calibrated and scoring is working (Phase 3), the optimal run frequency is:
- Weekly: standard mode
- Daily: high-intensity mode (during active job search sprints)
- Monthly: maintenance mode (after offer received — monitor market awareness only)

Daily runs require Apify rate limiting awareness. Run only LinkedIn (the most real-time board) daily; run the full multi-board search weekly.

### Scaling Dimension 4: Multiple Geographies

The current setup targets 8 European cities. If the search shifts (e.g., the candidate receives an offer and relocates to London), update `target_geography.preferred_locations` in the profile. No code changes required — all location parameters flow from the profile.

### Scaling Dimension 5: Multiple Profiles (Future, Phase 6+)

If the system is ever extended to serve multiple users, the architecture is already profile-driven. The database schema is single-user (no `user_id` column). To support multiple profiles, `jobs`, `scores`, `applications`, and all other tables would need a `profile_id` foreign key. This is a significant schema change. Not needed now, but worth knowing the current architecture does not prevent it — it just needs additive schema changes.

---

## 14. Risk Analysis

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LinkedIn blocks Apify scraper | Medium | High — primary source lost | Apify residential proxies mitigate most blocks. If blocked: reduce frequency, rotate keywords, use fallback sources. LinkedIn accounts for only ~50% of volume; other sources partially compensate. |
| Indeed domain structure changes break scraper | Low | Medium — 6 domains affected simultaneously | Apify actor maintained by community; structure changes are updated within days. Monitor `jobs_found` per run — drop to zero signals structural change. |
| WTTJ changes page structure | Medium | Low — secondary source | Use generic scraper; update selectors manually if needed. |
| Apify actor failure during run | Low | Low — partial data loss only | Each actor run is independent. Failure of one does not cascade. Log failed runs; retry on next scheduled run. |
| ATS API (Greenhouse, Lever) changes | Very Low | High — company watch mode breaks | These are stable public interfaces maintained by VC-backed ATS companies. They have not broken in 5+ years. Low risk. |
| Apify pricing increases significantly | Low | Medium — increases monthly cost | Monitor spend monthly. If Apify becomes too expensive, the generic web scraper approach is always a fallback for non-LinkedIn sources. |

### Data Quality Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Job descriptions are truncated | High (LinkedIn especially) | High — Scoring Agent needs full description | Set `fetchJobDetails: true` in LinkedIn actor. Implement minimum description length threshold (200 characters). Jobs with shorter descriptions are stored but flagged. |
| Duplicate jobs stored despite dedup | Medium | Medium — user sees redundant entries | Tier 2 deduplication (content hash) catches 90%+ of cross-board duplicates. Tier 1 catches same-board duplicates. Residual duplicates are visible but do not harm downstream steps. |
| `posted_date` missing or unparseable | High (especially for WTTJ, custom pages) | Low — filtering still works via fetched_date | Use `fetched_date` as fallback when `posted_date` is null. Never reject a job solely because posted_date is missing. |
| Company name variants create false non-duplicates | Medium | Low — cosmetic | "McKinsey & Company" vs "McKinsey" creates separate records. Harmless — both are stored, dedup_hash normalizes most variants. |
| Language detection misclassifies short descriptions | Medium | Low — some wrong language flags | Apply language filter only when description length > 150 characters. For shorter descriptions, default to accepting and letting the scoring agent handle it. |
| Role classification returns "unknown" for common titles | Medium | Medium — unclassified jobs miss weighting | Ongoing improvement of role_dictionary.json. Phase 2 stores unclassified jobs rather than rejecting them. The Phase 1 audit identified 4 missing common titles (HRBP, HR Business Partner, HR Manager, CPO) to fix before Phase 2 starts. |

### Business and Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Job market seasonality (August slump) | Certain | Medium — fewer listings in summer | Reduce run frequency in August. Use the time to calibrate prompts, update dictionaries, expand company watchlist. |
| High volume of low-quality results overwhelms review capacity | Medium | Medium — review fatigue | MIN_SCORE_THRESHOLD = 70 filters most. Shortlisted sheet shows only top jobs. Cap stored jobs per run to 30 highest-classified to prevent database bloat in Phase 2 before scoring is live. |
| Exclusion list too aggressive, filters out target roles | Low | High — misses opportunities | Log every rejected job with its rejection reason. Review rejection log weekly. Adjust exclusion keywords if legitimate target roles are being filtered. |
| Company career pages in watchlist become 404 or redirect | Medium | Low — affects individual companies | Verify URLs in watchlist quarterly. Add `is_active: false` flag to inactive entries rather than deleting. |

---

## 15. Recommended Architecture

### File Changes Required Before Implementation

**New files:**
- `agents/search_agent.py` — the Search Agent
- `data/company_watchlist.json` — company ATS monitoring list

**Existing files to modify:**
- `core/database.py` — add `get_job_by_url()`, `get_job_by_hash()` functions; update `insert_job()` to compute and store `dedup_hash`
- `data/candidate_profile.json` — add `"search_keyword_variants"` and `"exclude_title_keywords"` under `application_preferences`

**Database schema changes (before Phase 2 code runs):**
- `jobs` table: add columns `track TEXT`, `language TEXT`, `dedup_hash TEXT`, `role_category TEXT`
- Add index: `CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup_hash ON jobs(dedup_hash)`

**Data files to create before Phase 2 runs:**
- `data/company_watchlist.json` — initial list of 15–30 target companies with ATS provider and API URL

### Internal Component Structure

The Search Agent internally has five responsibilities:

```
search_agent.py
│
├── Query Builder
│   Reads profile → generates search query matrix
│   Pure function: profile → list of (keyword, location, contract_type) tuples
│
├── Source Connectors
│   One function per source:
│   │
│   ├── search_linkedin(query) → raw_results
│   ├── search_indeed(query, country) → raw_results
│   ├── search_wttj(query) → raw_results
│   ├── fetch_greenhouse(company) → raw_results   ← uses requests, not Apify
│   └── fetch_lever(company) → raw_results        ← uses requests, not Apify
│
├── Normalizer
│   One function per source:
│   normalize_linkedin(raw_result) → jobs schema dict
│   normalize_greenhouse(raw_result) → jobs schema dict
│   etc.
│
├── Filter Pipeline
│   filter_exclusions(job) → bool
│   filter_seniority(job) → bool
│   filter_language(job) → bool
│   filter_geography(job) → bool
│   All four applied in sequence — first failure = reject
│
└── Classifier + Deduplicator
    classify(job) → job with track + role_category filled in
    deduplicate(job) → (is_duplicate: bool, existing_id: int | None)
```

### Data Flow Diagram

```
profile.json + company_watchlist.json
           │
           ▼
    Query Builder
    (generate keyword × location matrix)
           │
     ┌─────┴─────┐
     ▼           ▼
Discovery     Watch Mode
Mode         (ATS APIs)
(Apify)      (requests)
     │           │
     └─────┬─────┘
           ▼
    Raw Results
    (all sources merged)
           │
           ▼
    ┌──────────────────────┐
    │   Filter Pipeline    │
    │  1. Exclusion check  │
    │  2. Seniority check  │
    │  3. Language check   │
    │  4. Geography check  │
    └──────────────────────┘
           │
           ▼
    ┌──────────────────────┐
    │   Normalize          │
    │   (source-specific   │
    │    field mapping)    │
    └──────────────────────┘
           │
           ▼
    ┌──────────────────────┐
    │   Classify           │
    │   role_loader        │
    │   .classify_title()  │
    └──────────────────────┘
           │
           ▼
    ┌──────────────────────┐
    │   Deduplicate        │
    │   Tier 1: URL        │
    │   Tier 2: Hash       │
    └──────────────────────┘
           │
     ┌─────┴─────┐
  Duplicate    New job
     │             │
  Log + skip    Insert to DB
                  + log
           │
           ▼
    searches table logged
    Summary printed
    jobs_master.xlsx updated
```

### Key Design Decisions

**Decision 1: Why Apify instead of direct scraping**
Direct scraping of LinkedIn or Indeed with `requests` gets blocked within hours. Apify's actor ecosystem provides managed scrapers with proxy rotation, maintained by the community, that remain functional even as job boards update their HTML structure. The cost ($5–$25/month) is justified by reliability.

**Decision 2: Why a company watchlist instead of searching ATS platforms by keyword**
You cannot keyword-search Greenhouse or Lever globally — there is no global endpoint. Each company's board is isolated. The watchlist approach is the only viable way to monitor specific high-priority companies on these platforms. It also surfaces roles that never syndicate to LinkedIn.

**Decision 3: Why store rejected jobs as logs, not as database rows**
Rejected jobs (wrong seniority, wrong language, excluded titles) are not stored in the `jobs` table. They are written to the run log. This keeps the database clean — every row in `jobs` is a legitimate candidate. Logs provide the audit trail if filtering is later questioned.

**Decision 4: Why dedup by content hash, not by (company + title) exact match**
Company name varies by source ("Siemens AG" vs "Siemens" vs "Siemens plc"). Job title varies too ("AI Strategy Intern" vs "Intern, AI Strategy"). Exact match would fail. The hash uses normalized company + normalized title + country code — robust enough to catch 90%+ of cross-board duplicates while tolerating natural variation.

**Decision 5: Why two-tier classification (jobs table + scores table)**
The Search Agent classifies job titles using the role dictionary at fetch time. This pre-classification is stored in `jobs.role_category`. The Scoring Agent will later run a more nuanced classification using both the title AND the description. The two may differ — and that discrepancy is itself informative (a job titled "HR Manager" but describing advanced analytics work may score higher than its title suggests). Keeping both classifications allows comparison.

**Decision 6: Why minimum description length threshold**
Some job postings contain less than 50 words of actual content. Scoring a 40-word description produces a low-confidence, unreliable score. Set minimum description length to 200 characters (~40 words). Store these jobs but flag them with `description_quality = "too_short"` in `raw_data`. The Scoring Agent can skip them or score them with reduced confidence.

---

## Pre-Implementation Checklist

Before writing any Phase 2 code, confirm:

**Phase 1 fixes (from audit) that must be completed first:**
- [ ] Add `get_job_by_url()` and `get_job_by_hash()` to `core/database.py`
- [ ] Fix short acronym false positives in `core/skill_loader.py` (ONA, TA, MI, BI, BD)
- [ ] Add HRBP, HR Business Partner, HR Manager, CPO to `data/role_dictionary.json`
- [ ] Remove 9 cross-category keyword duplicates from `data/skill_dictionary.json`
- [ ] Add UNIQUE constraint to `scores.job_id`
- [ ] Verify `.env` exists with both API keys

**Schema changes to `jobs` table:**
- [ ] Add `track TEXT` column
- [ ] Add `language TEXT` column
- [ ] Add `dedup_hash TEXT` column with UNIQUE index
- [ ] Add `role_category TEXT` column (pre-classification from Search Agent)

**Profile additions:**
- [ ] Add `"search_keyword_variants": []` to `target_roles` in `candidate_profile.json`
- [ ] Add `"exclude_title_keywords": ["hr generalist", "hr operations", "hr admin", ...]` to `application_preferences`

**Data files to create:**
- [ ] `data/company_watchlist.json` — initial list of target companies with ATS details

**Apify setup:**
- [ ] Apify account exists
- [ ] `APIFY_API_KEY` in `.env`
- [ ] Test `bebity/linkedin-jobs-scraper` manually in Apify Console with one query before coding

**Deliverable:**
Menu option 1 runs without error. Terminal prints a search summary. At least 10 new jobs are stored in the database. `outputs/exports/jobs_master_[date].xlsx` opens in Excel with correct structure. No hardcoded search terms in any Python file.
