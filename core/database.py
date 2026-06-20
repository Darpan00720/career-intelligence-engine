import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from core import config

_LEGAL_SUFFIXES_DB = re.compile(
    r"\b(?:gmbh|spa|s\.p\.a\.|ltd|limited|inc|incorporated|se|ag|nv|bv"
    r"|srl|sarl|sa|plc|llc|llp|kg|co\.?|group|holding|holdings)\b",
    re.IGNORECASE,
)


@contextmanager
def get_connection():
    """Context-managed connection to the configured database path.

    Commits on success, rolls back on exception, and ALWAYS closes (R3: no
    leaked connections). Transaction semantics are unchanged from the prior
    `with sqlite3.Connection` behavior; only the close-on-exit is added.
    All call sites use `with get_connection() as conn:` with materialized
    fetches, so prompt close is safe.
    """
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Concurrency: WAL lets readers run alongside a writer; busy_timeout makes
    # writers wait for the lock instead of failing fast with "database is locked".
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize() -> None:
    """Create all tables, apply migrations, and ensure required directories exist."""
    _ensure_directories()

    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT    NOT NULL,
                company      TEXT    NOT NULL,
                location     TEXT,
                job_board    TEXT,
                url          TEXT    UNIQUE,
                description  TEXT,
                posted_date  DATE,
                fetched_date DATE    DEFAULT (DATE('now')),
                is_expired    BOOLEAN DEFAULT 0,
                raw_data      TEXT,
                track         TEXT,
                language      TEXT,
                role_category TEXT,
                is_excluded   BOOLEAN DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scores (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id                  INTEGER NOT NULL REFERENCES jobs(id),
                role_category           TEXT,
                total_score             INTEGER,
                role_fit                INTEGER,
                skills_match            INTEGER,
                location_fit            INTEGER,
                seniority_fit           INTEGER,
                explanation             TEXT,
                matched_categories      TEXT,
                scored_at               DATETIME DEFAULT (DATETIME('now')),
                prompt_version          TEXT,
                dictionary_version      TEXT,
                role_dictionary_version TEXT
            );

            CREATE TABLE IF NOT EXISTS company_research (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id                INTEGER NOT NULL REFERENCES jobs(id),
                company               TEXT    NOT NULL,
                mission               TEXT,
                recent_news           TEXT,
                culture_notes         TEXT,
                key_people            TEXT,
                talking_points        TEXT,
                researched_at         DATETIME DEFAULT (DATETIME('now')),
                research_quality      INTEGER,
                research_quality_tier TEXT,
                personalization_mode  TEXT,
                prompt_version        TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id           INTEGER NOT NULL REFERENCES jobs(id),
                type             TEXT    NOT NULL,
                file_path        TEXT,
                file_name        TEXT,
                generated_at     DATETIME DEFAULT (DATETIME('now')),
                word_count       INTEGER,
                version          INTEGER DEFAULT 1,
                langgraph_run_id TEXT,
                prompt_version   TEXT
            );

            CREATE TABLE IF NOT EXISTS applications (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id           INTEGER NOT NULL REFERENCES jobs(id),
                status           TEXT    DEFAULT 'Found',
                applied_date     DATE,
                contact_name     TEXT,
                notes            TEXT,
                next_action      TEXT,
                next_action_date DATE,
                outcome          TEXT,
                last_updated     DATETIME DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS searches (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                query        TEXT,
                job_board    TEXT,
                jobs_found   INTEGER,
                searched_at  DATETIME DEFAULT (DATETIME('now')),
                filters_used TEXT
            );

            CREATE TABLE IF NOT EXISTS company_eligibility_profiles (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name                TEXT    NOT NULL UNIQUE,
                visa_friendliness_score     INTEGER NOT NULL DEFAULT 5,
                english_environment_score   INTEGER NOT NULL DEFAULT 5,
                international_student_score INTEGER NOT NULL DEFAULT 5,
                mba_friendliness_score      INTEGER NOT NULL DEFAULT 5,
                last_updated                DATETIME DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS jd_embeddings (
                id              INTEGER  PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER  NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                profile_version TEXT     NOT NULL,
                model_name      TEXT     NOT NULL DEFAULT 'all-MiniLM-L6-v2',
                embedding       BLOB     NOT NULL,
                computed_at     DATETIME DEFAULT (DATETIME('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_jd_embeddings_job_profile
                ON jd_embeddings(job_id, profile_version);

            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id                 TEXT    UNIQUE,
                started_at             DATETIME,
                completed_at           DATETIME,
                jobs_found             INTEGER DEFAULT 0,
                jobs_scored            INTEGER DEFAULT 0,
                companies_researched   INTEGER DEFAULT 0,
                documents_generated    INTEGER DEFAULT 0,
                applications_updated   INTEGER DEFAULT 0,
                status                 TEXT,
                duration_seconds       REAL,
                detail                 TEXT
            );

            CREATE TABLE IF NOT EXISTS review_queue (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id               INTEGER NOT NULL REFERENCES jobs(id),
                state                TEXT    DEFAULT 'Pending Review',
                recommendation       TEXT,
                resume_doc_id        INTEGER,
                cover_letter_doc_id  INTEGER,
                notes                TEXT,
                created_at           DATETIME DEFAULT (DATETIME('now')),
                updated_at           DATETIME DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS experiments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT    UNIQUE,
                name          TEXT,
                status        TEXT    DEFAULT 'running',
                variants      TEXT,
                winner        TEXT,
                created_at    DATETIME DEFAULT (DATETIME('now'))
            );

            CREATE TABLE IF NOT EXISTS experiment_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT    NOT NULL,
                variant       TEXT    NOT NULL,
                metric        TEXT    NOT NULL,
                value         REAL    NOT NULL,
                created_at    DATETIME DEFAULT (DATETIME('now'))
            );

            -- ── v5: multi-tenancy ────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS tenants (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id  TEXT    UNIQUE NOT NULL,
                name       TEXT,
                mode       TEXT    DEFAULT 'single',
                config     TEXT,
                created_at DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS platform_users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT    UNIQUE NOT NULL,
                tenant_id    TEXT    NOT NULL DEFAULT 'default',
                email        TEXT,
                role         TEXT    DEFAULT 'USER',
                api_key_hash TEXT,
                created_at   DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT    UNIQUE NOT NULL,
                tenant_id    TEXT    NOT NULL DEFAULT 'default',
                name         TEXT,
                created_at   DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id  TEXT    DEFAULT 'default',
                actor      TEXT,
                action     TEXT,
                entity     TEXT,
                detail     TEXT,
                created_at DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS usage_records (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id  TEXT    DEFAULT 'default',
                metric     TEXT    NOT NULL,
                amount     REAL    NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT (DATETIME('now'))
            );

            -- ── v5: event bus ────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS events_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        TEXT    UNIQUE NOT NULL,
                type            TEXT    NOT NULL,
                version         INTEGER DEFAULT 1,
                tenant_id       TEXT    DEFAULT 'default',
                idempotency_key TEXT,
                payload         TEXT,
                status          TEXT    DEFAULT 'published',
                error           TEXT,
                created_at      DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idem
                ON events_log(type, idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   TEXT,
                type       TEXT,
                tenant_id  TEXT    DEFAULT 'default',
                payload    TEXT,
                error      TEXT,
                created_at DATETIME DEFAULT (DATETIME('now'))
            );

            -- ── v5: persistent workflow engine ───────────────────────────────
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT    UNIQUE NOT NULL,
                tenant_id   TEXT    DEFAULT 'default',
                definition  TEXT    NOT NULL,
                status      TEXT    DEFAULT 'PENDING',
                detail      TEXT,
                created_at  DATETIME DEFAULT (DATETIME('now')),
                updated_at  DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS workflow_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT    NOT NULL,
                name        TEXT    NOT NULL,
                status      TEXT    DEFAULT 'PENDING',
                attempts    INTEGER DEFAULT 0,
                output      TEXT,
                error       TEXT,
                updated_at  DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS workflow_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     TEXT    NOT NULL,
                event      TEXT    NOT NULL,
                detail     TEXT,
                created_at DATETIME DEFAULT (DATETIME('now'))
            );

            -- ── v5: LLM cost tracking ────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS llm_costs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id        TEXT,
                tenant_id         TEXT    DEFAULT 'default',
                workflow_id       TEXT,
                agent             TEXT,
                provider          TEXT,
                model             TEXT,
                prompt_tokens     INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cost_usd          REAL    DEFAULT 0,
                latency_ms        REAL    DEFAULT 0,
                cached            INTEGER DEFAULT 0,
                created_at        DATETIME DEFAULT (DATETIME('now'))
            );

            -- ── v5.1: ML platform (feature store + model registry) ────────────
            CREATE TABLE IF NOT EXISTS ml_feature_sets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                version    INTEGER NOT NULL,
                features   TEXT,
                data       TEXT,
                created_at DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(name, version)
            );
            CREATE TABLE IF NOT EXISTS ml_models (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                version    INTEGER NOT NULL,
                stage      TEXT    DEFAULT 'staging',
                metadata   TEXT,
                lineage    TEXT,
                artifact   TEXT,
                created_at DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(name, version)
            );

            -- ── v5.4: AI agent platform (conversations, memory, agent state) ──
            CREATE TABLE IF NOT EXISTS conversations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    UNIQUE NOT NULL,
                tenant_id       TEXT    DEFAULT 'default',
                user_id         TEXT,
                title           TEXT,
                summary         TEXT,
                created_at      DATETIME DEFAULT (DATETIME('now')),
                updated_at      DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    NOT NULL,
                tenant_id       TEXT    DEFAULT 'default',
                role            TEXT    NOT NULL,
                content         TEXT,
                agent           TEXT,
                tokens          INTEGER DEFAULT 0,
                created_at      DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS agent_memories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id   TEXT    DEFAULT 'default',
                scope       TEXT,
                content     TEXT,
                embedding   TEXT,
                created_at  DATETIME DEFAULT (DATETIME('now')),
                expires_at  DATETIME
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     TEXT    UNIQUE NOT NULL,
                tenant_id  TEXT    DEFAULT 'default',
                status     TEXT    DEFAULT 'PENDING',
                state      TEXT,
                updated_at DATETIME DEFAULT (DATETIME('now'))
            );

            -- ── v5.5: Career Intelligence Engine ────────────────────────────
            CREATE TABLE IF NOT EXISTS career_profiles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id    TEXT    UNIQUE NOT NULL,
                tenant_id     TEXT    DEFAULT 'default',
                user_id       TEXT,
                full_name     TEXT,
                data          TEXT,           -- normalized resume JSON
                confidence    REAL    DEFAULT 0.0,
                created_at    DATETIME DEFAULT (DATETIME('now')),
                updated_at    DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS skills (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                name          TEXT    NOT NULL,
                slug          TEXT    NOT NULL,
                category      TEXT,           -- hard | soft | transferable
                version       INTEGER DEFAULT 1,
                created_at    DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(tenant_id, slug, version)
            );
            CREATE TABLE IF NOT EXISTS skill_relationships (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                source        TEXT    NOT NULL,
                target        TEXT    NOT NULL,
                relation      TEXT    NOT NULL,   -- prerequisite | related | similar
                weight        REAL    DEFAULT 1.0,
                version       INTEGER DEFAULT 1,
                created_at    DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS role_definitions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                slug          TEXT    NOT NULL,
                title         TEXT    NOT NULL,
                industry      TEXT,
                required_skills TEXT,            -- JSON list
                seniority     TEXT,
                version       INTEGER DEFAULT 1,
                created_at    DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(tenant_id, slug, version)
            );
            CREATE TABLE IF NOT EXISTS career_paths (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                from_role     TEXT    NOT NULL,
                to_role       TEXT    NOT NULL,
                difficulty    REAL    DEFAULT 0.0,
                typical_months INTEGER DEFAULT 0,
                created_at    DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                recommendation_id TEXT UNIQUE NOT NULL,
                tenant_id     TEXT    DEFAULT 'default',
                profile_id    TEXT,
                rec_type      TEXT,           -- job | career_path | learning
                target        TEXT,
                score         REAL    DEFAULT 0.0,
                confidence    REAL    DEFAULT 0.0,
                explanation   TEXT,
                created_at    DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS recommendation_feedback (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id         TEXT    DEFAULT 'default',
                recommendation_id TEXT,
                profile_id        TEXT,
                signal            TEXT,       -- accepted | rejected | viewed | applied
                weight            REAL    DEFAULT 1.0,
                created_at        DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                role_slug     TEXT,
                geography     TEXT,
                demand_count  INTEGER DEFAULT 0,
                data          TEXT,           -- JSON aggregates
                captured_at   DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS salary_benchmarks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                role_slug     TEXT,
                geography     TEXT,
                currency      TEXT    DEFAULT 'EUR',
                p25           REAL,
                p50           REAL,
                p75           REAL,
                sample_size   INTEGER DEFAULT 0,
                captured_at   DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS learning_paths (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                path_id       TEXT    UNIQUE NOT NULL,
                tenant_id     TEXT    DEFAULT 'default',
                profile_id    TEXT,
                target_role   TEXT,
                steps         TEXT,           -- ordered JSON list
                total_steps   INTEGER DEFAULT 0,
                completed_steps INTEGER DEFAULT 0,
                created_at    DATETIME DEFAULT (DATETIME('now'))
            );
            CREATE TABLE IF NOT EXISTS feature_flags (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id     TEXT    DEFAULT 'default',
                flag          TEXT    NOT NULL,
                enabled       BOOLEAN DEFAULT 0,
                rollout       REAL    DEFAULT 0.0,
                updated_at    DATETIME DEFAULT (DATETIME('now')),
                UNIQUE(tenant_id, flag)
            );

            CREATE INDEX IF NOT EXISTS idx_skills_tenant_cat
                ON skills(tenant_id, category);
            CREATE INDEX IF NOT EXISTS idx_skillrel_tenant_src
                ON skill_relationships(tenant_id, source);
            CREATE INDEX IF NOT EXISTS idx_roles_tenant
                ON role_definitions(tenant_id, slug);
            CREATE INDEX IF NOT EXISTS idx_recs_tenant_profile
                ON recommendations(tenant_id, profile_id);
            CREATE INDEX IF NOT EXISTS idx_recfb_tenant_rec
                ON recommendation_feedback(tenant_id, recommendation_id);
            CREATE INDEX IF NOT EXISTS idx_market_role_geo
                ON market_snapshots(tenant_id, role_slug, geography);
            CREATE INDEX IF NOT EXISTS idx_salary_role_geo
                ON salary_benchmarks(tenant_id, role_slug, geography);
            CREATE INDEX IF NOT EXISTS idx_learnpaths_profile
                ON learning_paths(tenant_id, profile_id);
        """)

    with get_connection() as conn:
        _migrate(conn)
    seed_company_eligibility_profiles()


def _ensure_directories() -> None:
    """Create log and output directories if they don't exist."""
    Path(config.LOGS_DIR).mkdir(parents=True, exist_ok=True)
    outputs = Path(config.OUTPUTS_DIR)
    for sub in ("resumes", "cover_letters", "exports", "documents", "archive"):
        (outputs / sub).mkdir(parents=True, exist_ok=True)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if column already exists in table (PRAGMA-based check)."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return True if a table exists (sqlite_master lookup)."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _safe_add_column(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    """Add column only if absent — prevents silent swallowing of real ALTER errors."""
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply schema migrations safely to both new and existing databases."""
    with conn:
        # Add columns to jobs — PRAGMA-checked so genuine ALTER errors are not swallowed
        for _col, _def in [
            ("dedup_hash",    "TEXT"),
            ("track",         "TEXT"),
            ("language",      "TEXT"),
            ("role_category", "TEXT"),
            ("is_excluded",   "BOOLEAN DEFAULT 0"),
        ]:
            _safe_add_column(conn, "jobs", _col, _def)

        # Add unique index for dedup_hash lookups (NULL values are excluded from uniqueness)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup_hash "
            "ON jobs(dedup_hash) WHERE dedup_hash IS NOT NULL"
        )

        # Phase 1.5: add eligibility gate columns to jobs
        for _col, _def in [
            ("language_gate",             "TEXT"),
            ("language_rejection_reason", "TEXT"),
            ("visa_gate",                 "TEXT"),
            ("visa_rejection_reason",     "TEXT"),
            ("eligibility_status",        "TEXT"),
        ]:
            _safe_add_column(conn, "jobs", _col, _def)

        # Phase 1.5b: eligibility score engine columns
        for _col, _def in [
            ("eligibility_score",      "INTEGER DEFAULT 0"),
            ("language_accessibility", "INTEGER DEFAULT 0"),
            ("visa_accessibility",     "INTEGER DEFAULT 0"),
            ("english_environment",    "INTEGER DEFAULT 0"),
            ("international_signals",  "INTEGER DEFAULT 0"),
        ]:
            _safe_add_column(conn, "jobs", _col, _def)

        # Phase 1.5c: language detection columns
        for _col, _def in [
            ("detected_language",           "TEXT"),
            ("language_risk",               "TEXT"),
            ("eligibility_review_required", "BOOLEAN DEFAULT 0"),
        ]:
            _safe_add_column(conn, "jobs", _col, _def)

        # v3: document versioning fingerprints — reuse documents instead of
        # regenerating when the job text, prompt, and research are unchanged.
        # Guarded: the documents table is created in initialize() before
        # _migrate runs; the guard keeps _migrate safe on partial test fixtures.
        if _table_exists(conn, "documents"):
            for _col, _def in [
                ("source_hash",   "TEXT"),
                ("prompt_hash",   "TEXT"),
                ("research_hash", "TEXT"),
                # v6: DB is content-authoritative; the file is a projection.
                ("content",       "TEXT"),
            ]:
                _safe_add_column(conn, "documents", _col, _def)

        # Phase 2: add scoring dimension columns to scores
        for _col, _def in [
            ("track_alignment_score",   "INTEGER DEFAULT 0"),
            ("mba_relevance_score",     "INTEGER DEFAULT 0"),
            ("company_quality_score",   "INTEGER DEFAULT 0"),
            ("intl_friendliness_score", "INTEGER DEFAULT 0"),
            ("pivot_bonus_score",       "INTEGER DEFAULT 0"),
            ("priority_bucket",         "TEXT"),
            ("scoring_metadata",        "TEXT"),
            ("semantic_similarity",     "REAL DEFAULT NULL"),
        ]:
            _safe_add_column(conn, "scores", _col, _def)

        # Enforce one score per job: deduplicate first, then add the constraint.
        # Keep the most recent score row per job_id.
        conn.execute("""
            DELETE FROM scores
            WHERE id NOT IN (
                SELECT MAX(id) FROM scores GROUP BY job_id
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_job_id ON scores(job_id)"
        )

        # Phase 1.6b: backfill Rejected score rows for jobs that failed eligibility
        # gates before the Rejected bucket was introduced. INSERT OR IGNORE is
        # idempotent — safe to run on every initialize() call.
        conn.execute("""
            INSERT OR IGNORE INTO scores
                (job_id, total_score, role_fit, skills_match, location_fit,
                 seniority_fit, explanation, matched_categories,
                 prompt_version, dictionary_version, role_dictionary_version,
                 priority_bucket)
            SELECT
                j.id, 0, 0, 0, 0, 0,
                COALESCE(
                    j.language_rejection_reason,
                    j.visa_rejection_reason,
                    'Eligibility gate failed'
                ),
                '{}', '2.0', '1.1', '1.1', 'Rejected'
            FROM jobs j
            WHERE j.eligibility_status = 'REJECTED'
        """)


# ── Validation ────────────────────────────────────────────────────────────────

_SCORE_RANGE = range(0, 101)  # 0..100 inclusive


def _validate_scores(total_score: int, role_fit: int, skills_match: int,
                     location_fit: int, seniority_fit: int) -> None:
    """Raise ValueError if any score value is outside 0–100."""
    fields = {
        "total_score": total_score,
        "role_fit": role_fit,
        "skills_match": skills_match,
        "location_fit": location_fit,
        "seniority_fit": seniority_fit,
    }
    bad = {k: v for k, v in fields.items() if not isinstance(v, int) or v not in _SCORE_RANGE}
    if bad:
        details = ", ".join(f"{k}={v!r}" for k, v in bad.items())
        raise ValueError(f"Score values must be integers 0–100. Invalid: {details}")


# ── Jobs ──────────────────────────────────────────────────────────────────────

def insert_job(title: str, company: str, location: str, job_board: str,
               url: str, description: str, posted_date: str = None,
               raw_data: str = None, dedup_hash: str = None) -> int | None:
    """Insert a job; silently ignores duplicates (same URL). Returns new row id or None."""
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO jobs
               (title, company, location, job_board, url, description,
                posted_date, raw_data, dedup_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, company, location, job_board, url, description,
             posted_date, raw_data, dedup_hash)
        )
        return cursor.lastrowid if cursor.lastrowid else None


def get_job_by_url(url: str) -> dict | None:
    """Return the job row matching this URL, or None if not found."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE url = ?", (url,)
        ).fetchone()
        return dict(row) if row else None


def get_job_by_hash(dedup_hash: str) -> dict | None:
    """Return the job row matching this content fingerprint, or None if not found."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE dedup_hash = ?", (dedup_hash,)
        ).fetchone()
        return dict(row) if row else None


def get_unscored_jobs() -> list[dict]:
    """Return active jobs that have no score and have not been rejected by the eligibility gate.

    When config.NON_ENGLISH_AUTO_REJECT is True, jobs whose detected_language
    is non-English (language_risk='HIGH') are also excluded from scoring.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT j.* FROM jobs j
               LEFT JOIN scores s ON s.job_id = j.id
               WHERE s.id IS NULL
                 AND j.is_expired = 0
                 AND COALESCE(j.eligibility_status, 'UNCHECKED') != 'REJECTED'"""
        ).fetchall()

    if config.NON_ENGLISH_AUTO_REJECT:
        return [dict(r) for r in rows if r["language_risk"] != "HIGH"]
    return [dict(r) for r in rows]


def update_job_eligibility(
    job_id: int,
    language_gate: str,
    language_rejection_reason: str,
    visa_gate: str,
    visa_rejection_reason: str,
    eligibility_status: str,
    eligibility_score:      int = 0,
    language_accessibility: int = 0,
    visa_accessibility:     int = 0,
    english_environment:    int = 0,
    international_signals:  int = 0,
) -> None:
    """Write eligibility gate results and score components onto a jobs row."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE jobs
               SET language_gate             = ?,
                   language_rejection_reason = ?,
                   visa_gate                 = ?,
                   visa_rejection_reason     = ?,
                   eligibility_status        = ?,
                   eligibility_score         = ?,
                   language_accessibility    = ?,
                   visa_accessibility        = ?,
                   english_environment       = ?,
                   international_signals     = ?
               WHERE id = ?""",
            (language_gate, language_rejection_reason,
             visa_gate, visa_rejection_reason,
             eligibility_status,
             eligibility_score, language_accessibility,
             visa_accessibility, english_environment,
             international_signals, job_id),
        )


def update_job_language_detection(
    job_id: int,
    detected_language: str,
    language_risk: str,
    eligibility_review_required: bool,
) -> None:
    """Write language detection results onto a jobs row."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE jobs
               SET detected_language           = ?,
                   language_risk               = ?,
                   eligibility_review_required = ?
               WHERE id = ?""",
            (detected_language, language_risk, int(eligibility_review_required), job_id),
        )


def set_job_eligibility_status(job_id: int, status: str) -> None:
    """Override eligibility_status — used by the scoring agent when auto-rejecting."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET eligibility_status = ? WHERE id = ?",
            (status, job_id),
        )


def update_job_classification(job_id: int, track: str, language: str, role_category: str) -> None:
    """Write track, language, and role_category onto an existing jobs row."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE jobs
               SET track = ?, language = ?, role_category = ?
               WHERE id = ?""",
            (track, language, role_category, job_id),
        )


def get_jobs_above_threshold(threshold: int) -> list[dict]:
    """Return jobs with total_score >= threshold, ordered by score descending."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT j.*, s.total_score, j.role_category, s.explanation
               FROM jobs j
               JOIN scores s ON s.job_id = j.id
               WHERE s.total_score >= ? AND j.is_expired = 0
               ORDER BY s.total_score DESC""",
            (threshold,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── Scores ────────────────────────────────────────────────────────────────────

def insert_score(
    job_id: int,
    role_category: str,
    total_score: int,
    role_fit: int,
    skills_match: int,
    location_fit: int,
    seniority_fit: int,
    explanation: str,
    matched_categories: dict,
    prompt_version: str,
    dictionary_version: str,
    role_dictionary_version: str,
    # Phase 2 dimensions (optional — defaults keep backward compatibility)
    track_alignment_score:   int   = 0,
    mba_relevance_score:     int   = 0,
    company_quality_score:   int   = 0,
    intl_friendliness_score: int   = 0,
    pivot_bonus_score:       int   = 0,
    priority_bucket:         str   = None,
    scoring_metadata:        str   = None,
    # Phase 3 — raw cosine similarity from semantic_matcher (None for keyword-only rows)
    semantic_similarity:     float = None,
) -> int:
    """
    Upsert a score for a job. If a score already exists for this job_id,
    it is replaced with the new values (re-score scenario).
    All core score fields are validated as integers in 0–100 before writing.
    """
    _validate_scores(total_score, role_fit, skills_match, location_fit, seniority_fit)

    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO scores
               (job_id, role_category, total_score, role_fit, skills_match,
                location_fit, seniority_fit, explanation, matched_categories,
                prompt_version, dictionary_version, role_dictionary_version,
                track_alignment_score, mba_relevance_score, company_quality_score,
                intl_friendliness_score, pivot_bonus_score,
                priority_bucket, scoring_metadata, semantic_similarity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   role_category            = excluded.role_category,
                   total_score              = excluded.total_score,
                   role_fit                 = excluded.role_fit,
                   skills_match             = excluded.skills_match,
                   location_fit             = excluded.location_fit,
                   seniority_fit            = excluded.seniority_fit,
                   explanation              = excluded.explanation,
                   matched_categories       = excluded.matched_categories,
                   scored_at                = DATETIME('now'),
                   prompt_version           = excluded.prompt_version,
                   dictionary_version       = excluded.dictionary_version,
                   role_dictionary_version  = excluded.role_dictionary_version,
                   track_alignment_score    = excluded.track_alignment_score,
                   mba_relevance_score      = excluded.mba_relevance_score,
                   company_quality_score    = excluded.company_quality_score,
                   intl_friendliness_score  = excluded.intl_friendliness_score,
                   pivot_bonus_score        = excluded.pivot_bonus_score,
                   priority_bucket          = excluded.priority_bucket,
                   scoring_metadata         = excluded.scoring_metadata,
                   semantic_similarity      = excluded.semantic_similarity""",
            (job_id, role_category, total_score, role_fit, skills_match,
             location_fit, seniority_fit, explanation,
             json.dumps(matched_categories),
             prompt_version, dictionary_version, role_dictionary_version,
             track_alignment_score, mba_relevance_score, company_quality_score,
             intl_friendliness_score, pivot_bonus_score,
             priority_bucket, scoring_metadata, semantic_similarity)
        )
        return cursor.lastrowid


def insert_rejected_score(job_id: int, rejection_reason: str) -> None:
    """
    Write a zero-score 'Rejected' bucket row for a job that failed an eligibility gate.
    Uses ON CONFLICT so re-runs are idempotent.
    """
    insert_score(
        job_id                  = job_id,
        role_category           = None,
        total_score             = 0,
        role_fit                = 0,
        skills_match            = 0,
        location_fit            = 0,
        seniority_fit           = 0,
        explanation             = rejection_reason,
        matched_categories      = {},
        prompt_version          = "2.0",
        dictionary_version      = "1.1",
        role_dictionary_version = "1.1",
        priority_bucket         = "Rejected",
    )


def get_scored_jobs_for_export() -> list[dict]:
    """
    Return all jobs that have a score row, ordered by priority bucket then
    total_score descending — for XLSX / CSV export.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT j.*,
                   s.total_score,
                   s.explanation,
                   s.priority_bucket,
                   s.track_alignment_score,
                   s.skills_match           AS skill_match,
                   s.mba_relevance_score,
                   s.company_quality_score,
                   s.intl_friendliness_score,
                   s.pivot_bonus_score,
                   s.scored_at
            FROM jobs j
            JOIN scores s ON s.job_id = j.id
            ORDER BY
                CASE s.priority_bucket
                    WHEN 'Apply Immediately' THEN 1
                    WHEN 'High Priority'     THEN 2
                    WHEN 'Medium Priority'   THEN 3
                    WHEN 'Low Priority'      THEN 4
                    WHEN 'Ignore'            THEN 5
                    WHEN 'Rejected'          THEN 6
                    ELSE 7
                END,
                s.total_score DESC
        """).fetchall()
    return [dict(r) for r in rows]


def get_all_scored_jobs_ranked() -> list[dict]:
    """
    Return every scored job ordered by total_score DESC, then date-found DESC —
    the canonical dataset for ranking, the jobs_master dashboard, and the Top
    Jobs view. Joins the latest application status (NULL when no application row
    exists yet). Ranks themselves are computed in core.ranking, not stored.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT j.*,
                   s.total_score,
                   s.priority_bucket,
                   s.explanation,
                   a.status AS application_status,
                   a.notes  AS application_notes
            FROM jobs j
            JOIN scores s ON s.job_id = j.id
            LEFT JOIN applications a ON a.job_id = j.id
            ORDER BY s.total_score DESC, j.fetched_date DESC, j.id DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ── Company Research ──────────────────────────────────────────────────────────

def insert_research(job_id: int, company: str, mission: str, recent_news: str,
                    culture_notes: str, key_people: str, talking_points: str,
                    research_quality: int, research_quality_tier: str,
                    personalization_mode: str, prompt_version: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO company_research
               (job_id, company, mission, recent_news, culture_notes, key_people,
                talking_points, research_quality, research_quality_tier,
                personalization_mode, prompt_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, company, mission, recent_news, culture_notes, key_people,
             talking_points, research_quality, research_quality_tier,
             personalization_mode, prompt_version)
        )
        return cursor.lastrowid


def get_researched_job_ids() -> set:
    """Return the set of job_ids that have at least one company_research row."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT job_id FROM company_research"
        ).fetchall()
    return {r["job_id"] for r in rows}


def get_research_for_job(job_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM company_research
               WHERE job_id = ? ORDER BY researched_at DESC LIMIT 1""",
            (job_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Documents ─────────────────────────────────────────────────────────────────

def insert_document(job_id: int, doc_type: str, file_path: str, file_name: str,
                    word_count: int, version: int, langgraph_run_id: str,
                    prompt_version: str,
                    source_hash: str = None, prompt_hash: str = None,
                    research_hash: str = None, content: str = None) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO documents
               (job_id, type, file_path, file_name, word_count, version,
                langgraph_run_id, prompt_version,
                source_hash, prompt_hash, research_hash, content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, doc_type, file_path, file_name, word_count, version,
             langgraph_run_id, prompt_version,
             source_hash, prompt_hash, research_hash, content)
        )
        return cursor.lastrowid


def get_latest_document(job_id: int, doc_type: str) -> dict | None:
    """Return the highest-version document row for a job + type, or None."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM documents
               WHERE job_id = ? AND type = ?
               ORDER BY version DESC, id DESC LIMIT 1""",
            (job_id, doc_type),
        ).fetchone()
        return dict(row) if row else None


# ── Applications ──────────────────────────────────────────────────────────────

def upsert_application(job_id: int, status: str = "Found") -> int:
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE applications
                   SET status = ?, last_updated = DATETIME('now')
                   WHERE job_id = ?""",
                (status, job_id)
            )
            return existing["id"]
        cursor = conn.execute(
            "INSERT INTO applications (job_id, status) VALUES (?, ?)",
            (job_id, status)
        )
        return cursor.lastrowid


def update_application(job_id: int, status: str = None, notes: str = None,
                       next_action: str = None, next_action_date: str = None,
                       contact_name: str = None, outcome: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE applications
               SET status           = COALESCE(?, status),
                   notes            = COALESCE(?, notes),
                   next_action      = COALESCE(?, next_action),
                   next_action_date = COALESCE(?, next_action_date),
                   contact_name     = COALESCE(?, contact_name),
                   outcome          = COALESCE(?, outcome),
                   last_updated     = DATETIME('now')
               WHERE job_id = ?""",
            (status, notes, next_action, next_action_date,
             contact_name, outcome, job_id)
        )


def get_application(job_id: int) -> dict | None:
    """Return the application row for a job, or None if none exists yet."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None


def ensure_application_row(job_id: int, status: str = "Not Started") -> bool:
    """Create an application row for a job if one does not already exist.

    Never overwrites an existing status (idempotent — safe to call every run).
    Returns True if a new row was created, False if one already existed.
    """
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO applications (job_id, status) VALUES (?, ?)",
            (job_id, status),
        )
        return True


def get_applications_overview() -> list[dict]:
    """Return all applications joined with job + score context for the tracker view,
    ordered by score DESC. Includes jobs that have an application row."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT a.job_id, a.status, a.notes, a.last_updated,
                   j.title, j.company, j.location, j.url,
                   s.total_score
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN scores s ON s.job_id = a.job_id
            ORDER BY COALESCE(s.total_score, 0) DESC, a.last_updated DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ── Company Eligibility Profiles ─────────────────────────────────────────────

def get_all_company_eligibility_profiles() -> list[dict]:
    """Return every row in company_eligibility_profiles, ordered by name."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM company_eligibility_profiles ORDER BY company_name"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_company_eligibility_profile(
    company_name: str,
    visa_friendliness_score:     int,
    english_environment_score:   int,
    international_student_score: int,
    mba_friendliness_score:      int,
) -> None:
    """Insert or update a single company eligibility profile."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO company_eligibility_profiles
               (company_name, visa_friendliness_score, english_environment_score,
                international_student_score, mba_friendliness_score, last_updated)
               VALUES (?, ?, ?, ?, ?, DATETIME('now'))
               ON CONFLICT(company_name) DO UPDATE SET
                   visa_friendliness_score     = excluded.visa_friendliness_score,
                   english_environment_score   = excluded.english_environment_score,
                   international_student_score = excluded.international_student_score,
                   mba_friendliness_score      = excluded.mba_friendliness_score,
                   last_updated                = DATETIME('now')""",
            (company_name, visa_friendliness_score, english_environment_score,
             international_student_score, mba_friendliness_score),
        )


def seed_company_eligibility_profiles() -> int:
    """
    Populate company_eligibility_profiles from data/company_eligibility_seed.json.
    Uses INSERT OR IGNORE — never overwrites manually curated rows.
    Returns count of newly inserted rows.
    """
    path = Path(config.BASE_DIR) / "data" / "company_eligibility_seed.json"
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    inserted = 0
    with get_connection() as conn:
        for entry in data.get("companies", []):
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO company_eligibility_profiles
                       (company_name, visa_friendliness_score, english_environment_score,
                        international_student_score, mba_friendliness_score)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        entry["company_name"],
                        entry["visa_friendliness_score"],
                        entry["english_environment_score"],
                        entry["international_student_score"],
                        entry["mba_friendliness_score"],
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    inserted += 1
            except (KeyError, sqlite3.Error):
                pass
    return inserted


# ── JD Embeddings ─────────────────────────────────────────────────────────────

def get_jd_embedding(job_id: int, profile_version: str) -> bytes | None:
    """Return the stored embedding BLOB for this (job_id, profile_version), or None."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT embedding FROM jd_embeddings
               WHERE job_id = ? AND profile_version = ?""",
            (job_id, profile_version),
        ).fetchone()
    return bytes(row["embedding"]) if row else None


def upsert_jd_embedding(
    job_id: int,
    profile_version: str,
    model_name: str,
    embedding_bytes: bytes,
) -> None:
    """Persist a JD embedding. Replaces any existing row for the same job + version."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO jd_embeddings (job_id, profile_version, model_name, embedding)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(job_id, profile_version) DO UPDATE SET
                   embedding   = excluded.embedding,
                   model_name  = excluded.model_name,
                   computed_at = DATETIME('now')""",
            (job_id, profile_version, model_name, embedding_bytes),
        )


def get_jd_embedding_count(profile_version: str | None = None) -> int:
    """Return number of cached JD embeddings, optionally filtered by profile_version."""
    with get_connection() as conn:
        if profile_version is not None:
            return conn.execute(
                "SELECT COUNT(*) FROM jd_embeddings WHERE profile_version = ?",
                (profile_version,),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM jd_embeddings").fetchone()[0]


def get_all_jd_embedding_blobs(profile_version: str) -> list[bytes]:
    """
    Return every JD embedding BLOB stored for this profile_version.
    Used by semantic_matcher.compute_calibrated_bounds() to derive
    percentile-based sim_to_score calibration from the real corpus.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT embedding FROM jd_embeddings WHERE profile_version = ?",
            (profile_version,),
        ).fetchall()
    return [bytes(r["embedding"]) for r in rows]


# ── JD Embedding Retention (Issue A) ──────────────────────────────────────────

def purge_old_profile_version_embeddings(current_profile_version: str) -> int:
    """
    Delete every jd_embeddings row whose profile_version differs from the
    current one. Returns the count of deleted rows.

    Rationale: embeddings are tied to the resume vector used during scoring.
    After a profile update the old embeddings are stale — they were computed
    against a different resume centroid and any cached cosine similarity is
    no longer meaningful.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM jd_embeddings WHERE profile_version != ?",
            (current_profile_version,),
        )
        return cursor.rowcount


def purge_aged_jd_embeddings(max_age_days: int = 90) -> int:
    """
    Delete jd_embeddings rows older than max_age_days. Returns deleted count.

    Rationale: jobs that have not been re-scored in 90 days are unlikely to
    become actionable again. Keeping their embeddings just inflates the table.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM jd_embeddings WHERE computed_at < DATETIME('now', ?)",
            (f"-{max_age_days} days",),
        )
        return cursor.rowcount


def purge_stale_jd_embeddings(
    current_profile_version: str,
    max_age_days: int = 90,
) -> tuple[int, int]:
    """
    Run both retention policies in sequence.

    1. purge_old_profile_version_embeddings — removes stale-version rows first
       so that only current-version embeddings remain for the age check.
    2. purge_aged_jd_embeddings — removes old rows among the survivors.

    Returns (n_version_deleted, n_aged_deleted).
    Call once per scoring_agent.run() before the scoring loop.
    """
    n_version = purge_old_profile_version_embeddings(current_profile_version)
    n_aged    = purge_aged_jd_embeddings(max_age_days)
    return n_version, n_aged


# ── Searches ──────────────────────────────────────────────────────────────────

def log_search(query: str, job_board: str, jobs_found: int,
               filters_used: dict = None) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO searches (query, job_board, jobs_found, filters_used)
               VALUES (?, ?, ?, ?)""",
            (query, job_board, jobs_found,
             json.dumps(filters_used) if filters_used else None)
        )
        return cursor.lastrowid


# ── Pipeline Runs (v3) ────────────────────────────────────────────────────────

def insert_pipeline_run(run: dict) -> int:
    """Persist a PipelineRun record (idempotent on run_id via upsert)."""
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO pipeline_runs
               (run_id, started_at, completed_at, jobs_found, jobs_scored,
                companies_researched, documents_generated, applications_updated,
                status, duration_seconds, detail)
               VALUES (:run_id, :started_at, :completed_at, :jobs_found, :jobs_scored,
                       :companies_researched, :documents_generated, :applications_updated,
                       :status, :duration_seconds, :detail)
               ON CONFLICT(run_id) DO UPDATE SET
                   completed_at         = excluded.completed_at,
                   jobs_found           = excluded.jobs_found,
                   jobs_scored          = excluded.jobs_scored,
                   companies_researched = excluded.companies_researched,
                   documents_generated  = excluded.documents_generated,
                   applications_updated = excluded.applications_updated,
                   status               = excluded.status,
                   duration_seconds     = excluded.duration_seconds,
                   detail               = excluded.detail""",
            run,
        )
        return cursor.lastrowid


def get_pipeline_runs(limit: int = 20) -> list[dict]:
    """Return the most recent pipeline runs, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Aggregate counts (v3 analytics) ───────────────────────────────────────────

def get_aggregate_counts() -> dict:
    """Return headline counts for the analytics engine in a single pass."""
    with get_connection() as conn:
        jobs_found  = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        jobs_scored = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]
        researched  = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM company_research"
        ).fetchone()[0]
        documents   = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    return {
        "jobs_found":   jobs_found,
        "jobs_scored":  jobs_scored,
        "researched":   researched,
        "documents":    documents,
    }


def get_status_counts() -> dict[str, int]:
    """Return {application_status: count} across the applications table."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def ensure_application_rows_bulk(job_ids: list[int], status: str = "Not Started") -> int:
    """Batch-create application rows for job_ids lacking one. Returns count created.

    Single connection + executemany — avoids per-row connection churn (v4 perf).
    """
    if not job_ids:
        return 0
    with get_connection() as conn:
        existing = {
            r["job_id"] for r in conn.execute(
                "SELECT job_id FROM applications WHERE job_id IN "
                f"({','.join('?' * len(job_ids))})", job_ids
            ).fetchall()
        }
        missing = [(jid, status) for jid in job_ids if jid not in existing]
        if missing:
            conn.executemany(
                "INSERT INTO applications (job_id, status) VALUES (?, ?)", missing
            )
        return len(missing)


# ── Review Queue (v4) ─────────────────────────────────────────────────────────

def enqueue_review(job_id: int, recommendation: str = None,
                   resume_doc_id: int = None, cover_letter_doc_id: int = None) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO review_queue
               (job_id, recommendation, resume_doc_id, cover_letter_doc_id)
               VALUES (?, ?, ?, ?)""",
            (job_id, recommendation, resume_doc_id, cover_letter_doc_id),
        )
        return cursor.lastrowid


def update_review_state(review_id: int, state: str, notes: str = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """UPDATE review_queue
               SET state = ?, notes = COALESCE(?, notes), updated_at = DATETIME('now')
               WHERE id = ?""",
            (state, notes, review_id),
        )


def get_review(review_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?", (review_id,)
        ).fetchone()
        return dict(row) if row else None


def list_reviews(state: str = None) -> list[dict]:
    with get_connection() as conn:
        if state:
            rows = conn.execute(
                """SELECT r.*, j.title, j.company
                   FROM review_queue r JOIN jobs j ON j.id = r.job_id
                   WHERE r.state = ? ORDER BY r.id DESC""", (state,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT r.*, j.title, j.company
                   FROM review_queue r JOIN jobs j ON j.id = r.job_id
                   ORDER BY r.id DESC"""
            ).fetchall()
    return [dict(r) for r in rows]


# ── Experiments (v4) ──────────────────────────────────────────────────────────

def upsert_experiment(experiment_id: str, name: str, variants: list[str],
                      status: str = "running") -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO experiments (experiment_id, name, variants, status)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(experiment_id) DO UPDATE SET
                   name = excluded.name, variants = excluded.variants,
                   status = excluded.status""",
            (experiment_id, name, json.dumps(variants), status),
        )


def set_experiment_winner(experiment_id: str, winner: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE experiments SET winner = ?, status = 'complete' WHERE experiment_id = ?",
            (winner, experiment_id),
        )


def get_experiment(experiment_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return dict(row) if row else None


def record_experiment_event(experiment_id: str, variant: str,
                            metric: str, value: float) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO experiment_events (experiment_id, variant, metric, value)
               VALUES (?, ?, ?, ?)""",
            (experiment_id, variant, metric, value),
        )
        return cursor.lastrowid


def get_experiment_events(experiment_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM experiment_events WHERE experiment_id = ?", (experiment_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Feedback / outcomes (v4) ──────────────────────────────────────────────────

def get_outcomes_joined() -> list[dict]:
    """Return every application joined with job + score context, for the
    feedback engine: company, role_category, score, status, outcome."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT a.job_id, a.status, a.outcome,
                   j.company, j.role_category, j.location, j.visa_gate, j.work_mode,
                   s.total_score
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            LEFT JOIN scores s ON s.job_id = a.job_id
        """).fetchall()
    return [dict(r) for r in rows]


def get_jobs_per_day(limit_days: int = 30) -> list[dict]:
    """Return [{day, count}] of jobs discovered per fetched_date (recent first)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT fetched_date AS day, COUNT(*) AS count
               FROM jobs WHERE fetched_date IS NOT NULL
               GROUP BY fetched_date ORDER BY fetched_date DESC LIMIT ?""",
            (limit_days,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_average_score() -> float:
    with get_connection() as conn:
        val = conn.execute("SELECT AVG(total_score) FROM scores").fetchone()[0]
    return round(val, 1) if val is not None else 0.0


def get_document_counts() -> dict:
    """Return {total, reused_versions} where reused_versions counts version>1 rows."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        revs = conn.execute("SELECT COUNT(*) FROM documents WHERE version > 1").fetchone()[0]
    return {"total": total, "reused_versions": revs}
