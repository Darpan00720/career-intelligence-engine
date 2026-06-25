# Architecture Decision Records

## What This Document Is

An Architecture Decision Record (ADR) captures a significant design choice: what was decided, why, what alternatives were considered, and what the consequences are. This document is the permanent record of every major architectural decision made in this project.

**Why keep this:** Months from now, when you or anyone else asks "why is it built this way?", the answer is here — not scattered across old messages or lost to memory. ADRs also protect against well-intentioned changes that would undo a decision that was correct the first time.

**ADR Statuses:**
- `Accepted` — decision is in effect
- `Superseded` — replaced by a later ADR (linked)
- `Deprecated` — no longer applies

---

## ADR-001: Use Python as the Programming Language

**Status:** Accepted
**Date:** 2026-06-09

### Context
A programming language was needed for the entire system: AI API calls, web scraping, database access, document generation, and Excel export. The choice of language determines which libraries are available, how readable the code is, and how easy it is to extend in the future.

### Decision
Python.

### Reasoning
Python is the dominant language for AI and data work. Every library this project needs has a native, well-maintained Python package:

| Need | Python library |
|---|---|
| Claude API | `anthropic` |
| Agent workflows | `langgraph` |
| Job search | Adzuna API + native ATS APIs (Greenhouse/Lever/Ashby/SmartRecruiters) |
| Database | `sqlite3` (built into Python) |
| Word documents | `python-docx` |
| Excel files | `openpyxl` |
| JSON handling | `json` (built into Python) |

Python syntax reads almost like English. A line like `if job.score > 70:` is understandable without programming knowledge. This matters for a project where the owner may eventually read or modify the code.

### Alternatives Considered

| Language | Why rejected |
|---|---|
| JavaScript / Node.js | Better suited for web applications and real-time servers; AI ecosystem is thinner than Python's |
| Java | Enterprise-grade, verbose, heavy tooling overhead; no advantage for a single-user local tool |
| Go | Fast and efficient; almost no AI/ML library ecosystem; would require custom clients for everything |
| R | Strong for statistical analysis; weak for agent orchestration, file generation, and web scraping |

### Consequences
**Positive:** Rich AI ecosystem, readable code, large community, easy to extend.
**Negative:** Python runs slower than compiled languages like Go or Java. This is not a concern here — the bottleneck is network calls to Claude and the job-search APIs, not CPU computation.

---

## ADR-002: Use SQLite as the Database

**Status:** Accepted
**Date:** 2026-06-09

### Context
The system needed a database to store jobs, scores, company research, documents, and application statuses. Dozens of tables could be involved. Relationships between them matter (a score belongs to a job; a document belongs to a job). Data must persist between runs.

### Decision
SQLite — a file-based relational database stored at `data/career_agent.db`.

### Reasoning
The key question when choosing a database is: who uses it, from where, and how many at once? For this project:
- One user (you)
- One machine (your laptop)
- One process at a time

SQLite is purpose-built for exactly this. It ships with Python — no installation required. The entire database is a single file you can copy, back up, or delete. Zero server processes. Zero configuration. Zero ongoing maintenance.

### Alternatives Considered

| Database | Why rejected |
|---|---|
| PostgreSQL | Requires a running server process; designed for concurrent multi-user access; operational overhead with no benefit for a local single-user tool |
| MySQL | Same concerns as PostgreSQL; also requires server |
| MongoDB | Document-oriented; better for flexible, rapidly-changing schemas; the data here (jobs, scores, applications) is highly structured and tabular — relational is the right model |
| Supabase / Cloud DB | External service dependency; introduces privacy concern (your job search data on a third-party server); costs money; offline use would break |

### Consequences
**Positive:** Zero setup, portable, file can be backed up with a simple copy, ships with Python.
**Negative:** Cannot support multiple concurrent users or a web application without migration to PostgreSQL. This migration is straightforward if the project ever scales — SQLite and PostgreSQL use near-identical SQL, so the code changes would be minimal.

---

## ADR-003: Store All Prompts as External Text Files

**Status:** Accepted
**Date:** 2026-06-09

### Context
AI agents must send instructions to Claude. These instructions — called "prompts" — could be written directly inside the Python files (hardcoded) or stored as separate files loaded at runtime. Both approaches work technically.

### Decision
All prompts stored as `.txt` files in `prompts/`. No prompt text inside any Python file. Enforced by Constraint 1 in `IMPLEMENTATION_PLAN.md`.

### Reasoning
Prompts and code change for completely different reasons:
- Code changes when a bug is fixed or a feature is added
- Prompts change when output quality needs improving

These changes happen on different timelines, by different people, for different reasons. Keeping them in separate files keeps those changes separate.

Additionally: prompts are the single most important variable in the system's output quality. The owner of this project needs to be able to read, understand, and improve them without knowing Python. A `.txt` file can be edited in Notepad. A string buried in a Python function cannot.

The `prompt_version` field in database tables records which prompt version produced each score or document — enabling before/after comparison when prompts are improved.

### Alternatives Considered

| Approach | Why rejected |
|---|---|
| Hardcoded strings in Python | Cannot edit without code knowledge; prompt and code changes are entangled; version history is unreadable |
| Prompts stored in database | Adds retrieval complexity; no benefit over files for a local tool; harder to edit without a GUI |
| Environment variables | Not suitable for long, structured prompt text; environment variables are for short configuration values |
| Separate prompts package | Over-engineering; same benefit as text files with added complexity |

### Consequences
**Positive:** Prompts can be improved without touching code; testable in Claude.ai directly; version history is explicit; transparent and auditable.
**Negative:** Two things to manage (code + prompt files) instead of one. Mitigated by: the separation is the point.

---

## ADR-004: Use `candidate_profile.json` as the Single Source of Truth for Candidate Data

**Status:** Accepted
**Date:** 2026-06-09

### Context
Multiple agents need to know about the candidate: name, experience, skills, target roles, geography. This data could be hardcoded in each agent that needs it, stored in environment variables, or centralized in a single file.

### Decision
Single JSON file at `data/candidate_profile.json`. All agents load it via `core/profile_loader.py`. No agent may contain hardcoded candidate information. Enforced by Constraint 2 in `IMPLEMENTATION_PLAN.md`.

### Reasoning
The classic "Don't Repeat Yourself" (DRY) principle: if the same fact exists in five places, it must be updated in five places. Miss one, and the system becomes inconsistent. The Scoring Agent might know about a new certification while the Cover Letter Agent writes as if it does not exist.

With a single source of truth: update `candidate_profile.json` once, and every agent immediately uses the new version.

JSON was chosen over other formats because:
- It is structured (unlike plain text)
- It is readable in any text editor (unlike a binary database)
- It supports nested data (experience has multiple roles; each role has multiple achievements)
- Python's `json` library handles it natively

### Alternatives Considered

| Approach | Why rejected |
|---|---|
| Hardcoded per agent | Violates DRY; inconsistency risk; requires code changes for profile updates |
| Environment variables | Not suitable for complex nested data (6 years of experience, multiple roles, quantified achievements) |
| Database table | More infrastructure than needed for configuration data; JSON file is simpler and more portable |
| YAML file | Valid alternative to JSON; chose JSON because it is more universally understood and natively supported in Python |

### Consequences
**Positive:** Single update point; consistent across all agents; readable without code knowledge; validation in `profile_loader.py` catches errors early.
**Negative:** If `candidate_profile.json` is malformed or incomplete, all agents that depend on it will fail. Mitigated by: `profile_loader.py` validates on load and produces a clear error message listing exactly which fields are empty or malformed.

---

## ADR-005: Use LangGraph for the Document Generation Workflow

**Status:** Accepted
**Date:** 2026-06-09

### Context
Document generation (resume + cover letter) involves multiple sequential AI steps: load context, generate resume, generate cover letter, quality-check both, retry if needed. This workflow could be implemented as simple sequential Python function calls, or as a formal graph-based workflow.

### Decision
LangGraph for Phase 4 (Document Generation Pipeline).

### Reasoning
A simple sequential script (`generate_resume()` → `generate_cover_letter()` → done) has three problems:

1. **No retry logic without messy code.** Implementing "if quality is poor, retry with feedback" in plain Python requires writing a custom state machine — effectively reinventing what LangGraph provides.

2. **Silent failures.** If the cover letter generation fails, there is no clean way to record which step failed, what the state was, and how to resume.

3. **No observability.** You cannot see which node the pipeline is currently executing, how many retries have happened, or what the intermediate outputs were.

LangGraph solves all three. Each step is a named node. Edges between nodes can be conditional (PASS → save; FAIL → retry). State flows through the graph so every node sees the outputs of previous nodes. Every run gets a `langgraph_run_id` stored in the database.

### Alternatives Considered

| Approach | Why rejected |
|---|---|
| Sequential Python calls | Works for the happy path; no retry loop; no observability; breaks messily |
| LangChain LCEL | Functional-style chaining; less explicit than a graph; conditional branching is more complex to implement |
| Custom state machine | Valid but reinvents LangGraph without its tooling or community support |
| Separate scripts per step | No state sharing between steps; complex file I/O required to pass data |

### Consequences
**Positive:** Built-in retry loop; explicit graph makes the workflow visible; full observability; `langgraph_run_id` enables tracing each document back to its pipeline run.
**Negative:** LangGraph is a framework dependency that must be maintained. Adds complexity to Phase 4 build. Mitigated by: LangGraph is the current industry standard for this pattern and is actively maintained.

---

## ADR-006: Include a Dedicated Company Research Agent

**Status:** Accepted
**Date:** 2026-06-09

### Context
Document generation could be based solely on the job description and candidate profile, or it could first gather information about the company to enable deeper personalization. A Company Research Agent adds a step, an API call, and a database table.

### Decision
Dedicated Company Research Agent that runs before the LangGraph document pipeline. Research is stored in `company_research` table and passed as context to the Resume and Cover Letter agents. A quality tier system (Poor / Moderate / Strong) governs how much personalization is permitted based on how much reliable information was found.

### Reasoning
A cover letter that says "I'm excited to join your company" is fundamentally weaker than one that says "Siemens' 2025 announcement of skills-based talent architecture is exactly the infrastructure challenge I've spent six years building toward." The difference is company research.

The quality tier system is the critical safeguard: without it, Claude risks inventing company facts when little is publicly available (hallucination). The tier system gates personalization depth behind evidence quality:
- Poor: generic professional framing, no invented specifics
- Moderate: confirmed facts only, limited personalization
- Strong: full personalization, specific references permitted

Research is cached per company — once researched, a company's dossier is reused for all applications to that company. The incremental cost is ~$0.008 per company, paid once.

### Alternatives Considered

| Approach | Why rejected |
|---|---|
| No company research; use job description only | Faster and cheaper; produces generic documents that do not stand out |
| Incorporate research into resume/cover letter prompts | One large combined prompt is harder to maintain, harder to cache, harder to improve independently |
| Use a web search API directly | More complex; would require parsing unstructured web pages; the Adzuna API + native ATS APIs already return structured listings — Claude handles synthesis |

### Consequences
**Positive:** Highly personalized documents; research cached per company; quality tier prevents hallucination.
**Negative:** Extra API call per company (~$0.008); extra pipeline step before documents can be generated; adds `company_research` table to database.

---

## ADR-007: Maintain Separate `jobs_master.xlsx` and `applications.xlsx`

**Status:** Accepted
**Date:** 2026-06-09

### Context
The system produces two types of structured data: job discovery data (every job found, with scores and documents) and application tracking data (applied jobs with status, notes, and next actions). These could be combined into one Excel file with two sheets, or kept as two completely separate files.

### Decision
Two separate Excel files with distinct purposes, updated by the same Tracker Agent.

### Reasoning
The two files have different audiences, different update cadences, and different use patterns:

| | `jobs_master.xlsx` | `applications.xlsx` |
|---|---|---|
| Purpose | Discovery and analysis | Daily task management |
| Updated | Automatically, every pipeline run | Manually + automatically |
| Audience | You reviewing opportunities | You managing in-progress applications |
| Size | Grows continuously (all jobs ever found) | Stays small (active applications only) |
| Primary action | Filter, sort, compare scores | Update status, check next actions |

Merging them produces a file that is too wide and too crowded for daily use as a task manager, and too action-oriented to function cleanly as an analytical reference. Two focused files, each optimized for one purpose, is better than one file that does both poorly.

### Alternatives Considered

| Approach | Why rejected |
|---|---|
| Single combined file | Too wide for daily use; two very different purposes fighting for the same layout |
| Database only, no Excel | Powerful but not portable; cannot be opened, shared, or reviewed without code |
| Single file with multiple tabs | Acceptable but tabs are accidentally switched; each file having an unambiguous single purpose is cleaner |
| Google Sheets (instead of Excel) | Requires internet connection and a Google account; Excel/xlsx works locally and is universally supported |

### Consequences
**Positive:** Each file has one clear purpose; `applications.xlsx` stays lean and action-focused; `jobs_master.xlsx` is the complete analytical record.
**Negative:** Two files to open and manage. Mitigated by: both are generated automatically — the user never has to manually create or format either one.
