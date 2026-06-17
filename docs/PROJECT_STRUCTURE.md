# Project Structure

## Architectural Rules (Read First)

Before reading the folder layout, understand the two rules that govern this entire project. Every file's location is a consequence of these rules.

---

### Rule 1: No Hardcoded Prompts

**The rule:** The text Claude receives as instructions must never appear inside a Python file. It must always be loaded from a `.txt` file in the `prompts/` folder.

**In practice:** When a Python agent needs to call Claude, it does this:
```
Load the prompt text from prompts/[agent]_prompt.txt
Combine it with the job data
Send to Claude
```

It never does this:
```
prompt = "You are a career advisor. Score this job..."   ← FORBIDDEN
```

**Why the folder structure reflects this:** The `prompts/` folder sits at the top level of the project — the same level as `agents/` and `core/`. It is a first-class component of the system, not a tucked-away detail. Its prominence signals that editing prompts is a legitimate, expected activity — not something that requires touching code.

---

### Rule 2: No Hardcoded Candidate Data

**The rule:** No agent may contain a variable like `MY_PROFILE = {...}` or any hardcoded personal information. All candidate data must be loaded from `data/candidate_profile.json`.

**In practice:** When an agent needs your profile, it does this:
```
Load candidate_profile.json from data/
Extract the relevant fields
Send to Claude alongside the job data
```

It never does this:
```
name = "Darpan"                 ← FORBIDDEN
mba = "POLIMI GSoM"            ← FORBIDDEN
experience = "6 years HR..."   ← FORBIDDEN
```

**Why the folder structure reflects this:** `candidate_profile.json` lives directly in `data/` — not buried in a subfolder, not embedded in code. It is the first thing someone reading the project should find when they ask "where is the user's information?"

---

## Folder and File Layout

Every item below has exactly one reason to exist. Nothing is optional.

```
ai-career-agent/
│
├── main.py                          ← Front door. The only file you run directly.
│
├── .env                             ← Private API keys. NEVER share or commit this file.
├── .env.example                     ← Safe template showing which keys are needed (no real values).
├── requirements.txt                 ← List of Python libraries the project needs to install.
│
├── prompts/                         ← ALL Claude instructions live here. Edit freely — no code required.
│   ├── scoring_prompt.txt           ← Instructions for scoring a job 0–100 across four dimensions.
│   ├── resume_prompt.txt            ← Instructions for writing an ATS-optimized tailored resume.
│   ├── cover_letter_prompt.txt      ← Instructions for writing a personalized cover letter.
│   └── company_research_prompt.txt  ← Instructions for researching a company before writing documents.
│
├── data/
│   ├── candidate_profile.json       ← YOUR PROFILE. Single source of truth. All agents read from here.
│   ├── skill_dictionary.json        ← Skills taxonomy. Maps job description keywords to standard categories.
│   ├── role_dictionary.json         ← Role taxonomy. Maps job titles to standardized role categories.
│   ├── career_agent.db              ← The database — one file storing all jobs, scores, and applications.
│   ├── profile/
│   │   └── master_resume.docx       ← Your base resume. The Resume Agent uses this as the starting point.
│   └── logs/                        ← Run logs. One file per pipeline run, for debugging and history.
│
├── agents/
│   ├── __init__.py                  ← (Technical: tells Python this folder is a module)
│   ├── orchestrator.py              ← Coordinator. Runs the full pipeline in the correct order.
│   ├── search_agent.py              ← Finds jobs on LinkedIn, Indeed, Glassdoor, InfoJobs, StepStone.
│   ├── scoring_agent.py             ← Loads scoring_prompt.txt + profile + skill_dictionary. Scores each job via Claude.
│   ├── company_research_agent.py    ← Loads company_research_prompt.txt. Researches approved companies.
│   ├── resume_agent.py              ← Loads resume_prompt.txt + profile. Generates tailored resumes.
│   ├── cover_letter_agent.py        ← Loads cover_letter_prompt.txt + profile. Generates cover letters.
│   ├── tracker_agent.py             ← Updates application status. Exports Excel files. No Claude calls.
│   └── langgraph_pipeline.py        ← Wires resume + cover letter + quality check into a LangGraph graph.
│
├── core/
│   ├── __init__.py
│   ├── config.py                    ← Central settings: score threshold, file paths, API model name.
│   ├── database.py                  ← ALL database reads and writes. No agent touches the DB directly.
│   ├── claude_client.py             ← ALL Claude API calls. Loads prompts. Handles retries and logging.
│   ├── profile_loader.py            ← Loads and validates candidate_profile.json. Used by all agents.
│   ├── prompt_loader.py             ← Loads prompt .txt files by name. Raises error if file is a stub.
│   ├── skill_loader.py              ← Loads skill_dictionary.json. Returns a keyword→category lookup.
│   ├── role_loader.py               ← Loads role_dictionary.json. Classifies job titles into role categories.
│   ├── document_builder.py          ← Builds Word (.docx) files from Claude's output using templates.
│   └── excel_exporter.py            ← Builds jobs_master.xlsx and applications.xlsx from database data.
│
├── config/                          ← Reserved for future YAML configuration files. Currently unused.
│
├── templates/
│   ├── resume_template.docx         ← Pre-formatted Word file. Resume Agent fills in the content sections.
│   └── cover_letter_template.docx   ← Pre-formatted Word file. Cover Letter Agent fills in the content.
│
├── outputs/
│   ├── resumes/                     ← All generated resumes saved here.
│   │   └── Resume_Darpan_[Company]_[Role]_[Date].docx
│   ├── cover_letters/               ← All generated cover letters saved here.
│   │   └── CoverLetter_Darpan_[Company]_[Role]_[Date].docx
│   └── exports/                     ← Excel exports saved here.
│       ├── jobs_master_[Date].xlsx
│       └── applications_[Date].xlsx
│
├── tests/
│   ├── test_search.py               ← Verifies the Search Agent returns valid job data.
│   ├── test_scoring.py              ← Verifies the Scoring Agent parses Claude's response correctly.
│   ├── test_resume.py               ← Verifies the Resume Agent produces a valid .docx file.
│   ├── test_cover_letter.py         ← Verifies the Cover Letter Agent produces a valid .docx file.
│   └── test_profile_loader.py       ← Verifies candidate_profile.json loads without errors.
│
└── docs/
    ├── MASTER_REQUIREMENTS.md       ← What you want to build (the original brief).
    ├── SYSTEM_ARCHITECTURE.md       ← How the system works — agents, data flow, tech choices, rules.
    ├── PROJECT_STRUCTURE.md         ← This file. What every folder and file does.
    ├── IMPLEMENTATION_PLAN.md       ← Step-by-step build plan with phases and deliverables.
    └── DECISIONS.md                 ← Architecture Decision Records. Why every major choice was made.
```

---

## Why Each Top-Level Folder Exists

### `prompts/`
This folder is the single location where you control what Claude is told to do. It contains one file per AI task in the system. The files are plain text — you do not need to understand Python to read or edit them.

**The four files and what each controls:**

**`scoring_prompt.txt`** — Contains the exact instructions Claude reads when scoring a job. It defines the four scoring dimensions (Role Fit, Skills Match, Location Fit, Seniority Fit), their point values, and how Claude should format its response. If you want Claude to weight your MBA more heavily, or to be more forgiving of senior roles, you change this file.

**`resume_prompt.txt`** — Contains the instructions for resume generation. It tells Claude to use ATS-friendly formatting, to incorporate keywords from the job description naturally, to reference specific company language, and how to structure the output so the document_builder can parse it into a Word file. If you want a different resume style or structure, you change this file.

**`cover_letter_prompt.txt`** — Contains the instructions for cover letter generation. It tells Claude to write in first person, to reference the company research, to connect your specific experience to the role, and to end with a clear call to action. If you want a different tone or length, you change this file.

**`company_research_prompt.txt`** — Contains the instructions for company research. It tells Claude what to look for (mission, culture, recent news, key people) and how to format the response so the Resume and Cover Letter agents can use specific sections. If you want the agent to look for different company information, you change this file.

**The rule enforced here:** Every agent file in `agents/` that calls Claude contains this pattern:
```
prompt_text = open("prompts/[name]_prompt.txt").read()
```
Never the prompt text itself.

---

### `agents/`
Where the intelligence lives. Each agent is a self-contained file with one responsibility. Because prompts are in `prompts/` and data is in `data/`, these files contain only the logic of *how* to execute the task — not *what* to say to Claude and not *who* is being helped.

Note the new file: `langgraph_pipeline.py`. This is not an agent itself — it is the LangGraph wiring that connects the Resume Agent, Cover Letter Agent, and Quality Check node into a single controllable workflow.

---

### `core/`
Shared utilities used by multiple agents. The key addition here is `profile_loader.py`.

**`config.py`** — The single file where all system settings live: score thresholds, file paths, the Claude model name, job board list, and retry limits. Every other file imports its settings from here. If you need to change the minimum score to 65 or switch to a newer Claude model, you change it in one place and the whole system picks it up.

**`profile_loader.py`** — A dedicated file whose only job is to load `data/candidate_profile.json`, validate that it is complete (no empty `FILL_IN` fields remaining), and return a clean Python object. Every agent that needs the profile calls this loader — it never opens the JSON file directly. This means validation logic lives in one place, and any future changes to the profile format are handled in one file.

**`prompt_loader.py`** — Loads a prompt `.txt` file by name (e.g. `scoring_prompt`) and returns its text. Raises a clear error if the file is still a stub (`# STUB`), preventing an agent from running before its prompt is written. This is the enforcer of Rule 1.

**`skill_loader.py`** — Loads `data/skill_dictionary.json` and builds a flat keyword → category lookup table for fast matching. The Scoring Agent calls this instead of reading the JSON directly. Result is cached so the dictionary is only read from disk once per run.

**`role_loader.py`** — Loads `data/role_dictionary.json` and builds a title → category lookup. The `classify_title()` function takes any job title string and returns the best matching role category. Uses both exact matching and partial substring matching so "Senior AI Strategy Intern" still maps to `ai_strategy`.

**`claude_client.py`** — The only place in the system where the Claude API is called. It accepts a prompt text (loaded from `prompts/`), a data payload, and returns Claude's response. All five AI agents go through this one gateway. This means logging, retry logic, and model version changes happen in one place.

**`database.py`** — The only place in the system that reads from or writes to `career_agent.db`. No agent touches the database directly. All data access goes through this gateway.

---

### `data/`
All inputs and persistent storage.

**`candidate_profile.json`** — Sits directly in `data/`, not in a subfolder. It is a first-class file. This placement reflects its importance: it is the single most important configuration file in the project.

**`skill_dictionary.json`** — The skills vocabulary used by the Scoring Agent. Contains 10 skill categories (product management, AI strategy, people analytics, talent acquisition, etc.), each populated with the specific keywords and phrases that appear in job descriptions. When the Scoring Agent reads a job posting, it looks up every skill keyword in this dictionary to map it to a standardized category — before passing anything to Claude. This means scoring is consistent regardless of how a job happens to phrase a skill. To add a new skill the agent should recognize, add a keyword to the relevant category in this file — no code change required.

**`role_dictionary.json`** — The job title vocabulary used by the Scoring Agent. Contains 8 role categories (product_management, ai_strategy, people_analytics, talent_acquisition, etc.), each populated with specific job title variants. When the Scoring Agent reads a job title, it maps it to a standardized category — "Associate Product Manager," "Product Owner," and "APM" all become `product_management`. This matters because role category determines how skills are weighted in scoring: a product management role should weight `product_management` and `ai_strategy` skills higher; an HR analytics role should weight `people_analytics` and `hr_analytics` higher. To add a new title variant you see in European job boards, add it to the relevant category — no code change required.

**`career_agent.db`** — The SQLite database. One file containing all 6 tables.

**`profile/master_resume.docx`** — Your base resume in Word format. The Resume Agent uses this as a structural reference before Claude generates the tailored content.

**`logs/`** — One log file per pipeline run. Contains a timestamp, how many jobs were processed, any errors encountered, and which Claude model was used. Useful for debugging and for tracking how the system performs over time.

---

### `core/config.py`
All system settings in one place. Every other file imports its settings from here — no file hard-codes a threshold or path on its own:
- `MIN_SCORE_THRESHOLD = 70` — jobs below this score do not get research or documents
- `MAX_APPLICATIONS_PER_WEEK = 15`
- `MAX_DOCUMENT_RETRIES = 2` — LangGraph retry cap
- `TARGET_JOB_BOARDS = ["linkedin", "indeed", "glassdoor", "infojobs", "stepstone"]`
- `CLAUDE_MODEL = "claude-opus-4-8"` — single place to upgrade the model
- All file paths: `PROFILE_PATH`, `SKILL_DICT_PATH`, `ROLE_DICT_PATH`, `PROMPTS_DIR`, `DB_PATH`, `OUTPUTS_DIR`

Centralizing these means you change a setting once and it applies everywhere.

### `config/`
Reserved for future YAML configuration files (e.g., job board filter presets, prompt parameter sets). Currently empty.

---

### `outputs/`
Everything the system generates. Organized into three subfolders so you can find what you need quickly. Files are named with company, role, and date — no ambiguity about which version belongs to which application.

This folder is excluded from git (`.gitignore`) — it contains personal documents that should not be version-controlled or accidentally shared.

---

### `docs/`
Project documentation. Every file here is a living document — updated as the architecture evolves. The new addition is `DECISIONS.md`.

**`DECISIONS.md`** — Contains seven Architecture Decision Records (ADRs), one per major design choice: why Python, why SQLite, why prompt files, why the profile JSON, why LangGraph, why a Company Research Agent, and why two separate Excel files. Each ADR follows a standard format: Context → Decision → Reasoning → Alternatives Considered → Consequences. When someone asks "why is it built this way?", the answer is in `DECISIONS.md`. When a future change is proposed, `DECISIONS.md` prevents re-litigating decisions that were already made correctly.

---

## Key Files — Detailed Explanation

### `data/candidate_profile.json`
The most important file in the project. Contains everything Claude needs to know about you. Structure:

```
{
  "personal":              name, email, phone, LinkedIn URL, location
  "education":             MBA at POLIMI GSoM — degree, dates, coursework
  "experience":            each role with responsibilities, skills, achievements
  "skills":                grouped by: HR core, analytics, AI/digital, strategy, soft
  "target_roles":          both tracks: Product & Strategy / Strategic HR
  "target_geography":      preferred cities, open to Europe, remote/hybrid flags
  "career_goals":          value proposition paragraph — what makes you distinct
  "application_preferences": score threshold, max applications, excluded companies
}
```

The `experience[].achievements` field is the most impactful. Achievements must be quantified (e.g. "Reduced time-to-hire by 30% across 5 business units") — not vague ("Improved hiring process"). Claude pulls directly from this field when writing resume bullets.

---

### `prompts/scoring_prompt.txt`
Plain text. Claude reads this before it reads any job description. It defines:
- The four scoring dimensions and their point weights
- What each dimension means (Role Fit = does the title match; Skills Match = does the background match, etc.)
- How to format the response (total score, sub-scores, explanation paragraph)
- Edge cases (e.g. how to handle roles that are adjacent but not exact matches)

You can open this file right now in any text editor and read exactly what Claude will be told.

---

### `core/profile_loader.py`
A small but critical file. Its jobs:
1. Open `data/candidate_profile.json`
2. Check that no fields still say `FILL_IN` (if they do, it stops and tells you which ones)
3. Return the profile as a clean, usable object
4. Cache the result — if three agents need the profile in one run, the file is only read once

Every agent that needs the profile calls `profile_loader.load()`. None of them open the JSON directly.

---

### `.env`
Contains two lines:
```
ANTHROPIC_API_KEY=sk-ant-...
APIFY_API_KEY=apify_api_...
```
Never written into any Python file. Never committed to git. `.env.example` is the safe version that shows the structure with placeholder values — this one is safe to share.

---

### `main.py`
The single file you interact with. It:
1. Shows the menu
2. Reads your choice
3. Calls the Orchestrator with the right instructions
4. Prints the result

You will never need to open any other file to use the system.
