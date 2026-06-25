"""Test package bootstrap.

R7 (Phase 8B): isolate a THIRD-PARTY warning so it cannot fail strict
`-W error::UserWarning` runs. starlette 1.3.1's TestClient import emits
`StarletteDeprecationWarning` (a UserWarning subclass) advising `httpx2`.
This originates entirely in starlette — NOT in our code. Per the allowed
remediation, we filter ONLY that specific third-party warning, in test
bootstrap only. No engine/production code is touched.
"""
import warnings

try:  # pragma: no cover - depends on installed starlette
    from starlette.exceptions import StarletteDeprecationWarning
    warnings.filterwarnings("ignore", category=StarletteDeprecationWarning)
except Exception:
    # Fallback: match by message if the class path changes across versions.
    warnings.filterwarnings(
        "ignore",
        message=r".*httpx.*starlette\.testclient.*deprecated.*",
    )


# --- Database bootstrap for fresh checkouts / CI ------------------------------
# Many tests assume an initialised, non-empty database. Locally
# data/career_agent.db already exists (built up by real runs); on a fresh
# checkout — e.g. CI — it does not, which is why those tests went green locally
# but red in CI ("no such table: jd_embeddings", scored_jobs == []). Create the
# schema and seed a handful of eligible sample jobs when the table is empty.
# Idempotent and safe: the schema uses CREATE TABLE IF NOT EXISTS, and seeding
# is skipped whenever the jobs table already has rows, so a populated local DB
# is left completely untouched.
def _bootstrap_test_db() -> None:  # pragma: no cover - environment bootstrap
    try:
        from core import database
        database.initialize()
        with database.get_connection() as conn:
            empty = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        if not empty:
            return
        # Seed eligible sample jobs at the exact ids the graph-integration tests
        # read from the DB (SeedJobProvider uses ids 19, 248, 13, 55, 424). Rows
        # carry PASS gates + ELIGIBLE so the pipeline scores them and produces
        # recommendations. The same rows also feed the DbJobProvider-based tests
        # (ORDER BY id LIMIT N), so no separate generic seed is needed.
        seed = [
            (19,  "AI Product Intern",            "Northwind", "Amsterdam, Netherlands", "ai_strategy",
             "AI Product Management internship for our English-speaking product team in "
             "Amsterdam. Product strategy, analytics and machine-learning features. A "
             "graduate internship; English is our working language."),
            (248, "Product Strategy Intern",      "Helios",    "Milan, Italy",           "product_management",
             "Internship in product and business strategy at our Milan office. English-"
             "speaking international team; data analysis and go-to-market strategy."),
            (13,  "Business Strategy Graduate",    "Aurora",    "Dublin, Ireland",        "business_strategy",
             "Graduate programme in business strategy, working with product teams on "
             "market analysis and growth initiatives. English required."),
            (55,  "Digital Transformation Intern", "Vela",      "Berlin, Germany",        "digital_transformation",
             "Internship supporting digital transformation and innovation projects across "
             "the business. English-speaking environment, graduate level."),
            (424, "People Analytics Intern",       "Cobalt",    "Remote - Europe",        "people_analytics",
             "Remote internship in people analytics and workforce insights. Strong data "
             "and analytics skills. English working language."),
        ]
        with database.get_connection() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO jobs
                   (id, title, company, location, job_board, url, description, is_expired,
                    role_category, language_gate, language_rejection_reason, visa_gate,
                    visa_rejection_reason, eligibility_status, eligibility_score,
                    language_accessibility, visa_accessibility, english_environment,
                    international_signals, detected_language, language_risk)
                   VALUES (?,?,?,?,?,?,?,0,?, 'PASS','', 'PASS','', 'ELIGIBLE',70,
                           35,30,7,5,'English','LOW')""",
                [(i, t, co, loc, "seed", f"https://seed.local/{i}", d, rc)
                 for (i, t, co, loc, rc, d) in seed],
            )
    except Exception:
        # Never let bootstrap break test collection in unusual environments.
        pass


_bootstrap_test_db()
