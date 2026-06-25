# System Architecture

## What This System Does

This is an autonomous AI agent that runs on your computer. You give it a command (e.g. "find me 20 product manager internships in Europe"), and it handles everything else: searching job boards, researching companies, scoring each job against your profile, writing tailored resumes and cover letters, saving all documents, and tracking your applications — all without you touching a single job board.

---

## Architectural Rules

These two rules govern how every agent in this system is built. They are non-negotiable constraints, not suggestions. Every future change to this system must respect them.

---

### Rule 1: Prompts Are Files, Not Code

**The rule:** No AI agent may contain prompt text inside a Python file. Every prompt must be loaded from a `.txt` file in the `prompts/` folder at runtime.

**The four prompt files:**

| File | Used by | What it instructs Claude to do |
|---|---|---|
| `prompts/scoring_prompt.txt` | Scoring Agent | Score a job 0–100 across four dimensions; explain the score in plain English |
| `prompts/resume_prompt.txt` | Resume Agent | Write an ATS-optimized resume tailored to the job description and company research |
| `prompts/cover_letter_prompt.txt` | Cover Letter Agent | Write a specific, personalized cover letter using company research and resume |
| `prompts/company_research_prompt.txt` | Company Research Agent | Research a company and return structured talking points for document generation |

**Why this rule exists — five reasons:**

**1. You can improve the system without touching code.**
If Claude's scoring feels off — too harsh on career-changers, not weighting your MBA correctly — you open `scoring_prompt.txt` in any text editor, rewrite the relevant sentence, and the agent immediately uses the new logic. No Python knowledge needed. No risk of accidentally breaking something else.

**2. Prompts and code change for different reasons.**
Code changes when a bug is fixed or a new feature is added. Prompts change when you want better output quality. These are different kinds of changes made by different people on different timelines. Keeping them in separate files keeps those concerns separate.

**3. You can test prompts without running the system.**
You can copy the contents of `scoring_prompt.txt`, paste it directly into Claude.ai, add a sample job description, and see exactly what output you would get. You can iterate on the prompt ten times in five minutes without spinning up the full system.

**4. Version control becomes meaningful.**
When the quality of your resumes improves, you can look at the git history for `resume_prompt.txt` and see exactly what changed and when. If a change makes things worse, you can revert just the prompt file — not a tangled Python file full of unrelated logic.

**5. The system is transparent and auditable.**
Anyone reading the project can understand exactly what instructions are being sent to Claude by reading four plain text files. There is no hunting through Python code to find buried strings.

**What the rule prevents:**

```
# THIS IS FORBIDDEN — hardcoded prompt in Python:
prompt = "Score this job from 0 to 100 based on..."

# THIS IS REQUIRED — loaded from a file:
prompt = open("prompts/scoring_prompt.txt").read()
```

---

### Rule 2: Candidate Information Lives in One File

**The rule:** No agent may contain hardcoded information about the candidate. All personal, educational, professional, and preference data must be read from `data/candidate_profile.json` at runtime.

**Why this rule exists — four reasons:**

**1. Update once, all agents use the new version.**
When you complete a new project, add a certification, or change your target roles, you edit `candidate_profile.json`. Every agent — Scoring, Resume, Cover Letter, Research — reads that file fresh every time it runs. Nothing else needs to change.

**2. Agents contain logic, not data.**
An agent's job is to *do something* — score, research, write. It should not also be the storage location for your work history. Mixing data and logic creates a system that is hard to understand and harder to maintain.

**3. Claude gets a consistent, complete picture every time.**
Every Claude call that needs your profile sends the same file. There is no risk of the Scoring Agent knowing about your MBA while the Cover Letter Agent works from an outdated version you forgot to update in both places.

**4. The file is readable and maintainable without code knowledge.**
JSON is a structured text format. You can open `candidate_profile.json` in any text editor and read it like a form. Editing your profile is editing a document — not modifying a program.

**What the rule prevents:**

```
# THIS IS FORBIDDEN — hardcoded profile in Python:
MY_PROFILE = {
    "name": "Darpan",
    "mba": "POLIMI GSoM",
    "experience": "6 years HR..."
}

# THIS IS REQUIRED — loaded from a file:
profile = json.load(open("data/candidate_profile.json"))
```

---

## Technology Choices

| Layer | Tool | Why |
|---|---|---|
| Language | Python | Industry standard for AI agents; readable and beginner-friendly |
| AI Brain | Claude API (Anthropic) | Powers scoring, research, resume writing, cover letter generation |
| Agent Workflow | LangGraph | Manages the document generation pipeline as a controllable flowchart |
| Database | SQLite | A single file on your computer — no server needed, zero setup |
| Documents | python-docx | Generates real Word (.docx) resume and cover letter files |
| Excel Export | openpyxl | Writes `jobs_master.xlsx` and `applications.xlsx` |
| Job Search | Adzuna API + native ATS APIs | Adzuna (free REST API) aggregates many boards; Greenhouse/Lever/Ashby/SmartRecruiters via their official public APIs — no scraping |
| Prompts | `.txt` files in `prompts/` | AI instructions stored as editable text — never hardcoded in Python |
| Candidate Data | `data/candidate_profile.json` | Single source of truth — never hardcoded in any agent |
| Skills Taxonomy | `data/skill_dictionary.json` | Normalizes job description keywords into standard categories before Claude analysis — improves scoring consistency |
| Role Taxonomy | `data/role_dictionary.json` | Normalizes job titles into standardized role categories before scoring — ensures "Product Owner" and "Associate PM" are treated identically |
| Research Quality | 0–10 scale with three tiers | Controls personalization depth — prevents hallucinated company facts in documents |
| Secrets | `.env` file | API keys stored privately, never in code |

---

## What LangGraph Is and Why It's Here

Think of LangGraph as a **flowchart engine for AI agents**.

Without LangGraph, the Resume Agent runs, then the Cover Letter Agent runs. If something goes wrong, the whole thing fails silently. There is no way to check quality, loop back and retry, or inject a review step.

With LangGraph, the document generation pipeline is drawn as a proper graph — boxes (nodes) connected by arrows (edges). Each box is one step. Each arrow is a decision. You can see exactly which step failed, add a quality-check node that says "if the resume is too generic, regenerate it," and control the entire flow from one diagram.

LangGraph is used in Phase 4 (Document Generation) because that is where the workflow has the most complexity: research feeds into resume, resume feeds into cover letter, and both may need revision based on a quality check.

---

## System Diagram

```
You
 │
 │  (type a command or use the menu)
 ▼
┌─────────────────────────────────────────┐
│              Orchestrator               │  ← The "Project Manager"
│  Decides which agents to run and when.  │
│  Reads: data/candidate_profile.json     │
└────────────────┬────────────────────────┘
                 │
    ┌────────────▼────────────┐
    │      Search Agent       │  Step 1: Finds jobs on LinkedIn, Indeed,
    │                         │          Glassdoor, InfoJobs, StepStone
    │  Reads: profile.json    │          (no prompt file — no AI call)
    └────────────┬────────────┘
                 │ (saves jobs to database)
    ┌────────────▼────────────┐
    │      Scoring Agent      │  Step 2: Normalizes title via role dict,
    │                         │          normalizes skills via skill dict,
    │  Prompt: scoring_       │          then scores 0–100 via Claude.
    │  prompt.txt             │
    │  Data: profile.json +   │
    │  role_dictionary.json + │
    │  skill_dictionary.json  │
    └────────────┬────────────┘
                 │ (you review scores, approve top jobs)
    ┌────────────▼────────────┐
    │  Company Research Agent │  Step 3: Researches each approved company:
    │                         │          culture, news, mission, key people
    │  Prompt: company_       │
    │  research_prompt.txt    │
    └────────────┬────────────┘
                 │ (research saved to database)
    ┌────────────▼────────────────────────────────────────────┐
    │           LangGraph Document Pipeline                   │
    │                                                         │
    │  ┌──────────────────┐     ┌──────────────────────┐      │
    │  │  Resume Agent    │────▶│ Cover Letter Agent   │      │
    │  │                  │     │                      │      │
    │  │ Prompt: resume_  │     │ Prompt: cover_letter_│      │
    │  │ prompt.txt       │     │ prompt.txt           │      │
    │  │ Data: profile +  │     │ Data: profile +      │      │
    │  │ company research │     │ research + resume    │      │
    │  └──────────────────┘     └──────────┬───────────┘      │
    │          ▲                           │                   │
    │          │               ┌───────────▼───────────┐      │
    │          │               │   Quality Check Node  │      │
    │          └───────────────│   Score < threshold   │      │
    │       (retry with        │   → loop back         │      │
    │        feedback)         └───────────┬───────────┘      │
    └───────────────────────────────────────┼─────────────────┘
                                            │ (documents saved to outputs/)
    ┌───────────────────────────────────────▼─────────────────┐
    │                    Tracker Agent                        │
    │  Updates status. Exports:                               │
    │  → jobs_master.xlsx      → applications.xlsx            │
    └─────────────────────────────────────────────────────────┘
```

---

## The Six Agents — What Each One Does

### 1. Search Agent
**Its one job:** Find job listings.

- Connects to LinkedIn Jobs, Indeed, Glassdoor, InfoJobs (Italy), and StepStone (Europe)
- Reads target roles and geography from `data/candidate_profile.json`
- Filters by location: Europe-wide, with preference for remote / Milan / major hubs
- Saves every discovered job to the database — avoids re-fetching the same job twice
- **No prompt file** — the Search Agent does not call Claude; it queries job boards directly

---

### 2. Scoring Agent
**Its one job:** Decide which jobs are worth your time — using a structured skill-matching pipeline, not pure LLM guesswork.

- **Loads prompt from:** `prompts/scoring_prompt.txt`
- **Loads candidate data from:** `data/candidate_profile.json`
- **Loads skills vocabulary from:** `data/skill_dictionary.json`

**The five-step scoring pipeline:**

```
Step A:  Normalize job title via role_dictionary.json
         → map to standardized role category
           (e.g. "Associate Product Manager" → product_management)
           (e.g. "People Analytics Analyst" → people_analytics)
           ↓
Step B:  Parse job description
         → extract required skills, keywords, and qualifications
           ↓
Step C:  Cross-reference against skill_dictionary.json
         → map extracted terms to standardized skill categories
           (e.g. "headcount planning" + "workforce scenarios" → workforce_planning)
           ↓
Step D:  Compare role category + mapped skill categories against candidate profile
         → calculate role-weighted skill overlap
           (product_management role → weight product_management and ai_strategy categories higher)
           (people_analytics role → weight people_analytics and hr_analytics categories higher)
           ↓
Step E:  Send structured analysis to Claude via scoring_prompt.txt
         → Claude interprets context, weighs fit, produces final score + explanation
```

**Why five steps instead of one?** Without normalization, two identical jobs score differently based on phrasing — "headcount planning" vs "workforce forecasting" are the same skill, but raw text comparison treats them differently. The skill dictionary solves that. The role dictionary adds a second layer: "Associate Product Manager" and "Product Owner" are evaluated against the same criteria, not as arbitrary title strings. Together, the two dictionaries make scores consistent, comparable, and explainable across all jobs.

- Claude scores 0–100 across four dimensions:
  - **Role Fit (30 pts)** — Does the title match your target roles?
  - **Skills Match (40 pts)** — How many skill_dictionary categories overlap with your profile?
  - **Location Fit (15 pts)** — Is it in Europe? Remote-friendly? Near Milan?
  - **Seniority Fit (15 pts)** — Is it intern / junior level, or too senior?
- Saves total score, sub-scores, matched skill categories, and plain-English explanation to the `scores` table

**Example output:**
> Job: AI Strategy Intern @ Siemens, Munich | Score: 84/100
> Role category: ai_strategy
> Matched skill categories: workforce_planning (direct), people_analytics (direct), ai_strategy (adjacent), business_strategy (adjacent)
> Why: Strong alignment with your MBA in AI & Digital Transformation and HRBP experience. Role explicitly mentions workforce planning — a direct match. Slight deduction: German preferred.

---

### 3. Company Research Agent
**Its one job:** Build a research dossier on each approved company, classify its quality, and gate personalization depth accordingly.

- **Loads prompt from:** `prompts/company_research_prompt.txt`
- Runs only on jobs that scored above your threshold (default: 70)
- Sends company name + job description to Claude using the research prompt
- Claude returns structured research: mission, recent news, culture, key people, talking points
- Assigns a **research quality score** (0–10) and classifies into one of three tiers
- Saves dossier + quality tier to the `company_research` table
- Quality tier is passed to the LangGraph pipeline and controls personalization depth

#### Research Quality Classification

| Score | Tier | Meaning | Typical company |
|---|---|---|---|
| 0–3 | **Poor** | Little verifiable information found | Small startup, obscure company, no media coverage |
| 4–6 | **Moderate** | Some reliable information found | Mid-size company, regional brand, limited public news |
| 7–10 | **Strong** | Rich, verifiable information found | Large enterprise, public company, active media presence |

#### Rules by Quality Tier

**Poor (0–3) — Generic professional framing only:**
- Do NOT reference specific company initiatives
- Do NOT make claims about company culture that cannot be verified
- Do NOT create statements about the company's future direction
- Use only verified facts: company size, main industry, exact job description language
- Documents are professional and credible but do not pretend to know the company

**Moderate (4–6) — Company-aware personalization:**
- Enable resume tailoring using confirmed facts from the dossier
- Enable cover letter references to mission and values (verified)
- Do NOT reference specific recent events unless clearly confirmed
- Documents are company-aware but conservative with specifics

**Strong (7–10) — Full deep personalization:**
- Enable strategic resume references to specific company initiatives
- Enable cover letter hooks referencing recent announcements, partnerships, or leadership statements
- Enable specific named references to people, products, or events
- Documents feel like you spent a weekend preparing

#### How Quality Tier Affects Generated Documents

**Example: AI Strategy Intern @ Nova Analytics (small startup — research_quality = 2, Poor)**

*Resume bullet:*
> "Developed AI-driven workforce planning frameworks applicable to high-growth, resource-constrained environments"

*Cover letter opening:*
> "I am applying for the AI Strategy Intern role at Nova Analytics. My MBA in AI & Digital Transformation and six years operationalizing people analytics give me a strong foundation for contributing to your team from day one."

No invented facts. Professional and credible.

---

**Example: AI Strategy Intern @ Siemens (large enterprise — research_quality = 9, Strong)**

*Resume bullet:*
> "Built workforce intelligence platform aligning talent strategy to operational KPIs — directly applicable to Siemens' announced shift toward skills-based talent architecture outlined in their 2024 People Strategy report"

*Cover letter opening:*
> "When Siemens' CHRO outlined the company's pivot to AI-augmented HR operations at the 2025 HR Tech Forum, I recognized it as the exact intersection I have been building toward — six years operationalizing people analytics at scale, now grounded in an MBA focused on AI & Digital Transformation at POLIMI GSoM."

Specific, cited, confident.

---

### 4. Resume Agent *(inside LangGraph)*
**Its one job:** Write a tailored, ATS-optimized resume for each job.

- **Loads prompt from:** `prompts/resume_prompt.txt`
- **Loads candidate data from:** `data/candidate_profile.json`
- Receives job description + company research from the LangGraph pipeline context
- Asks Claude to produce a resume with the exact keywords from the job posting
- Saves the resume as a `.docx` Word file: `Resume_Darpan_[Company]_[Role]_[Date].docx`

---

### 5. Cover Letter Agent *(inside LangGraph)*
**Its one job:** Write a personalized cover letter for each job.

- **Loads prompt from:** `prompts/cover_letter_prompt.txt`
- **Loads candidate data from:** `data/candidate_profile.json`
- Receives job description + company research + tailored resume from pipeline context
- References specific company details from the research dossier
- Saved as: `CoverLetter_Darpan_[Company]_[Role]_[Date].docx`

---

### 6. Tracker Agent
**Its one job:** Keep track of everything and produce the two core Excel deliverables.

- Updates application status: Found → Shortlisted → Applied → Interview → Offer → Rejected
- Exports **`jobs_master.xlsx`** — every job ever found, with scores and document links
- Exports **`applications.xlsx`** — active applications only, with status and next actions
- **No prompt file** — the Tracker Agent does not call Claude; it reads the database and writes Excel

---

## The Two Core Excel Deliverables

### `jobs_master.xlsx`
**What it is:** The complete record of every job the agent has ever found.

| What it contains | Why |
|---|---|
| All jobs from all searches | Nothing gets lost — even low-scoring jobs are recorded |
| Score (total + 4 sub-scores) | You can sort and filter by fit |
| Company research summary | One-line preview of what the agent found |
| Links to generated documents | Click directly to open the resume or cover letter |
| Job board source and URL | Original posting, one click away |
| Date found vs date posted | Helps you prioritize fresh postings |

---

### `applications.xlsx`
**What it is:** Your live application tracker — focused only on jobs you are actively pursuing.

| What it contains | Why |
|---|---|
| Application status | Where each application stands right now |
| Applied date | When you submitted |
| Next action + due date | What you need to do next |
| Interview notes | Free-text field for notes from calls / interviews |
| Contact name | Recruiter or hiring manager |
| Outcome | Final result when the process ends |

---

## `data/candidate_profile.json` — The Single Source of Truth

**Path:** `data/candidate_profile.json`

Every agent that needs to know about you reads from this one file. No agent stores your information internally. No agent has a `MY_PROFILE` variable. If you update your profile — new experience, new target role, new location — you change it once and all agents automatically use the updated version.

**Contents:**

| Section | What it stores |
|---|---|
| `personal` | Name, email, phone, LinkedIn, location |
| `education` | MBA at POLIMI GSoM — institution, degree, field, dates, coursework |
| `experience` | Each role — title, company, dates, responsibilities, skills, achievements (with numbers) |
| `skills` | Grouped by category: HR core, analytics, AI/digital, strategy, soft skills, certifications |
| `target_roles` | Both tracks: Product & Strategy and Strategic HR — with seniority preference |
| `target_geography` | Preferred cities, open to all Europe, remote/hybrid flags, relocation flag |
| `career_goals` | One-paragraph value proposition — what makes you distinct |
| `application_preferences` | Min score threshold (default 70), max applications per week, excluded companies |

The more detailed and accurate this file is — especially the `achievements` field with real numbers — the better every scored job, tailored resume, and cover letter will be.

---

## Data Flow — What Happens Step by Step

```
Step 1:  Search Agent reads profile.json (target roles + geography)
         → finds jobs → saves to database → jobs_master.xlsx created

Step 2:  Scoring Agent loads scoring_prompt.txt + profile.json + role_dictionary.json + skill_dictionary.json
         → normalizes job title to role category → normalizes job skills to skill categories
         → sends role-weighted structured analysis to Claude
         → saves scores + matched role category + matched skill categories → jobs_master.xlsx updated

Step 3:  You review scores in jobs_master.xlsx
         → approve jobs above your threshold

Step 4:  Company Research Agent loads company_research_prompt.txt
         → researches approved companies → assigns quality score 0–10
         → classifies tier (Poor / Moderate / Strong) → saves dossiers to database

Step 5:  LangGraph pipeline runs for each approved job:
         Pipeline reads research quality tier → sets personalization mode
         Resume Agent loads resume_prompt.txt + profile.json → writes resume
         Cover Letter Agent loads cover_letter_prompt.txt + profile.json → writes letter
         Quality Check Node: PASS → save | FAIL → retry (max 2 times)
         → Documents saved to outputs/ with quality tier noted in documents table

Step 6:  Tracker Agent updates jobs_master.xlsx with document links
Step 7:  You submit applications → update status
Step 8:  applications.xlsx reflects live status: Applied, Interview, Offer, Rejected
```

---

## Privacy and Security

- API keys (Claude, Adzuna) are in `.env` — never committed to git, never shared
- `data/candidate_profile.json` lives locally on your machine only
- The database (`career_agent.db`) is a single file on your computer — no cloud, no servers
- No data leaves your machine except API calls to Claude (job description + profile) and Adzuna (search queries)
- `prompts/` files contain no personal data — they are safe to share or version-control

---

## What "Running the Agent" Looks Like

```
AI Career Agent
───────────────────────────────
1. Search for new jobs
2. Score all unscored jobs
3. Research approved companies
4. Generate documents (LangGraph pipeline)
5. View top jobs (score 70+)
6. Update application status
7. Export jobs_master.xlsx
8. Export applications.xlsx
9. Run full pipeline (steps 1–4 automatically)
0. Exit
```
