"""
Semantic skill matching via sentence-transformers (all-MiniLM-L6-v2).

Replaces keyword cluster scoring in scorer._score_skill_match() with cosine
similarity between a resume embedding and a per-job JD embedding.

Cache hierarchy (fastest → slowest):
  1. Module-level model singleton  — loaded once per process, never reloaded
  2. In-memory JD dict             — {(job_id, profile_version): np.ndarray}
  3. SQLite jd_embeddings table    — survives across scoring runs (non-Ignore only)
  4. Disk resume embedding          — data/embeddings/resume_<sha256>.npy
     Key is SHA-256 of profile content (Issue #3), not profile_version, so the
     file regenerates automatically when candidate_profile.json changes even
     without a version bump.

Install requirement (propose before using):
    pip install sentence-transformers torch --index-url https://download.pytorch.org/whl/cpu
"""
from __future__ import annotations

import hashlib
import json as _json
import logging
from pathlib import Path

from core import config

try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    _NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBED_DIR  = Path(config.BASE_DIR) / "data" / "embeddings"

# Cosine similarity → score mapping bounds (static fallback).
# Used when corpus calibration has fewer than _MIN_CALIBRATION_SAMPLES entries.
# Typical intra-domain range for this model: 0.30 (unrelated) – 0.82 (near-identical).
_SIM_FLOOR = 0.30
_SIM_CEIL  = 0.82

# Minimum stored similarities required before corpus-based calibration is applied.
_MIN_CALIBRATION_SAMPLES = 20

# Active calibrated bounds used by compute_semantic_match.
# Defaults to the static constants; updated by set_calibration() at run-time.
# Reset to defaults by clear_cache() so tests start from a clean state.
_active_floor: float = _SIM_FLOOR
_active_ceil:  float = _SIM_CEIL

# In-memory JD embedding cache for the current process lifetime.
_jd_mem_cache: dict[tuple[int | None, str], "np.ndarray"] = {}

# Lazy-loaded model singleton — None until first _embed() call.
_model = None


# ── Model ─────────────────────────────────────────────────────────────────────

def _load_model():
    """Return the SentenceTransformer singleton, loading it on first call."""
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
        logger.info("Loaded embedding model: %s", _MODEL_NAME)
        return _model
    except ImportError:
        return None


def _embed(texts: list[str]) -> "np.ndarray":
    """
    Embed a list of strings.
    Returns float32 ndarray of shape (N, 384) with L2-normalised rows.
    Raises RuntimeError if numpy or sentence-transformers is not installed.
    """
    if not _NUMPY_AVAILABLE:
        raise RuntimeError("numpy not installed. Run: pip install numpy")
    model = _load_model()
    if model is None:
        raise RuntimeError(
            "sentence-transformers not installed. "
            "Run: pip install sentence-transformers torch "
            "--index-url https://download.pytorch.org/whl/cpu"
        )
    return model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,   # unit vectors → dot product == cosine similarity
        show_progress_bar=False,
    )


def warm_embedding_model() -> bool:
    """
    Trigger model loading without embedding any real text.

    Call at application startup (e.g. scoring_agent.run) to eliminate cold-start
    latency on the first scoring request. The model singleton is shared process-wide,
    so this one call amortises the ~2-5 s load time across the full scoring batch.

    Returns True if the model loaded successfully, False if unavailable.
    """
    if not _NUMPY_AVAILABLE:
        logger.info("warm_embedding_model: numpy not available, skipping.")
        return False
    model = _load_model()
    if model is None:
        logger.info("warm_embedding_model: sentence-transformers not available, skipping.")
        return False
    try:
        _embed(["warmup"])
        logger.info("Embedding model warm-up complete: %s", _MODEL_NAME)
        return True
    except RuntimeError:
        return False


# ── Profile content hash (Issue #3) ───────────────────────────────────────────

def _profile_content_hash(profile: dict) -> str:
    """
    Stable 16-char hex prefix of SHA-256(sorted profile JSON).

    Using content hash (not profile_version) as the disk-cache key means the
    resume embedding file regenerates automatically whenever candidate_profile.json
    is edited, regardless of whether the profile_version field was bumped.
    """
    serialized = _json.dumps(profile, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# ── Resume embedding ──────────────────────────────────────────────────────────

def _resume_disk_path(content_hash: str) -> Path:
    return _EMBED_DIR / f"resume_{content_hash}.npy"


def get_resume_embedding(profile: dict) -> "np.ndarray":
    """
    Return the resume centroid embedding for this profile.

    Disk cache key is the SHA-256 of the profile JSON content — the file
    regenerates whenever candidate_profile.json changes, even without a version bump.
    """
    content_hash = _profile_content_hash(profile)
    path         = _resume_disk_path(content_hash)

    if path.exists():
        return np.load(str(path))

    phrases = _extract_resume_phrases(profile)
    if not phrases:
        raise ValueError("candidate_profile.json has no embeddable skill phrases.")

    embeddings = _embed(phrases)               # (N, 384) normalised
    centroid   = embeddings.mean(axis=0)
    centroid   = centroid / np.linalg.norm(centroid)   # re-normalise after mean

    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(path), centroid.astype(np.float32))
    logger.info("Saved resume embedding → %s  (%d phrases)", path.name, len(phrases))
    _cleanup_stale_resume_caches(content_hash)   # remove orphaned old-hash files
    return centroid.astype(np.float32)


def _cleanup_stale_resume_caches(active_hash: str) -> int:
    """
    Delete all resume_*.npy files in _EMBED_DIR except the currently active one.

    Called immediately after saving a new resume embedding so that profile edits
    do not accumulate orphaned files on disk (one stale file per profile edit).

    Returns the count of deleted files.
    """
    deleted = 0
    try:
        for p in _EMBED_DIR.glob("resume_*.npy"):
            if p.stem != f"resume_{active_hash}":
                p.unlink()
                deleted += 1
                logger.info("Purged stale resume embedding: %s", p.name)
    except OSError as exc:
        logger.warning("Error during stale resume cache cleanup: %s", exc)
    return deleted


def _extract_resume_phrases(profile: dict) -> list[str]:
    """
    Flatten all skill strings, role labels, and narrative text from the profile
    into a single list of embeddable phrases.

    Order (high signal first so mean centroid is pulled toward priority items):
      value_proposition · education coursework + thesis · experience bullets
      · target roles · all skill lists
    """
    phrases: list[str] = []

    vp = profile.get("value_proposition", "")
    if vp:
        phrases.append(vp)

    for edu in profile.get("education", []):
        phrases.extend(edu.get("key_coursework", []))
        thesis = edu.get("thesis_or_project", "")
        if thesis:
            phrases.append(thesis)

    for exp in profile.get("experience", []):
        phrases.extend(exp.get("responsibilities", []))

    for roles in profile.get("target_roles", {}).values():
        if isinstance(roles, list):
            phrases.extend(str(r) for r in roles if not r.startswith("FILL_IN"))

    for skill_list in profile.get("skills", {}).values():
        if isinstance(skill_list, list):
            phrases.extend(str(s) for s in skill_list if not str(s).startswith("FILL_IN"))

    return [p.strip() for p in phrases if p.strip()]


# ── JD embedding ──────────────────────────────────────────────────────────────

def _jd_text(job: dict) -> str:
    """Build the text fed to the encoder for a job record."""
    title = (job.get("title") or "").strip()
    desc  = (job.get("description") or "")[:1500]
    return f"{title}. {desc}".strip()


def get_jd_embedding(
    job: dict,
    profile_version: str,
    persist: bool = True,
) -> "np.ndarray":
    """
    Return the JD embedding for this job + profile version combination.

    Cache check order: in-memory → SQLite → compute.

    Args:
        persist: If True (default), write newly computed embeddings to SQLite.
                 scorer._score_skill_match passes False to defer persistence —
                 only non-Ignore/non-Rejected jobs are flushed by scoring_agent
                 via flush_jd_to_db(), keeping the DB lean (Issue #2).
    """
    job_id    = job.get("id")
    cache_key = (job_id, profile_version)

    # Level 1: in-memory
    if cache_key in _jd_mem_cache:
        return _jd_mem_cache[cache_key]

    # Level 2: SQLite (skipped for anonymous jobs without an id)
    if job_id is not None:
        from core import database
        blob = database.get_jd_embedding(job_id, profile_version)
        if blob is not None:
            emb = np.frombuffer(blob, dtype=np.float32).copy()
            _jd_mem_cache[cache_key] = emb
            return emb

    # Level 3: compute
    text = _jd_text(job)
    emb  = _embed([text])[0].astype(np.float32)

    _jd_mem_cache[cache_key] = emb

    if persist and job_id is not None:
        from core import database
        database.upsert_jd_embedding(
            job_id          = job_id,
            profile_version = profile_version,
            model_name      = _MODEL_NAME,
            embedding_bytes = emb.tobytes(),
        )

    return emb


def flush_jd_to_db(job_id: int | None, profile_version: str) -> bool:
    """
    Persist the in-memory JD embedding for this job to SQLite.

    Called by scoring_agent._write_score after bucket determination:
    only non-Ignore and non-Rejected jobs are flushed so that the
    jd_embeddings table stays bounded to actionable jobs (Issue #2).

    Returns True if an embedding was found in memory and persisted.
    """
    if job_id is None:
        return False
    cache_key = (job_id, profile_version)
    if cache_key not in _jd_mem_cache:
        return False
    emb = _jd_mem_cache[cache_key]
    from core import database
    database.upsert_jd_embedding(
        job_id          = job_id,
        profile_version = profile_version,
        model_name      = _MODEL_NAME,
        embedding_bytes = emb.tobytes(),
    )
    return True


# ── Similarity → score ────────────────────────────────────────────────────────

def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    """
    Cosine similarity for two L2-normalised (unit) vectors.
    Both inputs must already be unit vectors (as produced by _embed / get_*_embedding).
    """
    return float(np.dot(a, b))


def sim_to_score(
    sim: float,
    floor: float = _SIM_FLOOR,
    ceil:  float = _SIM_CEIL,
) -> int:
    """
    Map cosine similarity to a 0-25 integer score.

    Accepts optional floor/ceil for corpus-calibrated mapping (Issue #6).
    Defaults to the module-level constants [0.30, 0.82].

    [below floor] → 0
    [floor, ceil] → linear 0-25
    [above ceil]  → 25
    """
    clamped    = max(floor, min(ceil, sim))
    normalised = (clamped - floor) / (ceil - floor)
    return round(normalised * 25)


def calibrate_sim_bounds(similarities: list[float]) -> tuple[float, float]:
    """
    Compute empirical P10/P90 calibration bounds from scored-job similarities.

    Uses a percentile approach so the full 0-25 score range is utilised across
    the real distribution rather than against fixed universal constants.

    Args:
        similarities: List of cosine similarity values (one per scored job).

    Returns (floor, ceil) — P10 and P90 of the input distribution.
    Falls back to (_SIM_FLOOR, _SIM_CEIL) when:
      - numpy is not available
      - fewer than _MIN_CALIBRATION_SAMPLES values are provided
      - P90 - P10 < 0.05 (degenerate distribution — corpus is near-uniform)
    """
    if not _NUMPY_AVAILABLE or len(similarities) < _MIN_CALIBRATION_SAMPLES:
        return _SIM_FLOOR, _SIM_CEIL
    arr   = np.array(similarities, dtype=np.float32)
    floor = float(np.percentile(arr, 10))
    ceil  = float(np.percentile(arr, 90))
    if ceil - floor < 0.05:   # degenerate — fallback to static constants
        return _SIM_FLOOR, _SIM_CEIL
    return floor, ceil


def compute_calibrated_bounds(profile: dict, profile_version: str) -> tuple[float, float]:
    """
    Full calibration pipeline: load all stored JD embeddings from SQLite,
    compute cosine similarity against the resume, then call calibrate_sim_bounds.

    Falls back to (_SIM_FLOOR, _SIM_CEIL) when embeddings are unavailable or
    fewer than _MIN_CALIBRATION_SAMPLES exist in the database.
    """
    if not _NUMPY_AVAILABLE:
        return _SIM_FLOOR, _SIM_CEIL
    try:
        from core import database
        blobs = database.get_all_jd_embedding_blobs(profile_version)
        if len(blobs) < _MIN_CALIBRATION_SAMPLES:
            return _SIM_FLOOR, _SIM_CEIL
        resume_emb = get_resume_embedding(profile)
        sims = [
            cosine_similarity(resume_emb, np.frombuffer(b, dtype=np.float32))
            for b in blobs
        ]
        return calibrate_sim_bounds(sims)
    except Exception as exc:
        logger.warning("Calibration failed, using defaults: %s", exc)
        return _SIM_FLOOR, _SIM_CEIL


def set_calibration(floor: float, ceil: float) -> None:
    """
    Set the active calibration bounds used by compute_semantic_match.

    Call once per scoring run (e.g. in scoring_agent.run) after
    compute_calibrated_bounds() derives corpus-aware P10/P90 values.
    Falls through to static defaults when the corpus is too small.
    """
    global _active_floor, _active_ceil
    _active_floor = floor
    _active_ceil  = ceil
    logger.debug("sim_to_score calibration set: floor=%.3f  ceil=%.3f", floor, ceil)


def get_calibration() -> tuple[float, float]:
    """Return the active (floor, ceil) bounds currently used by compute_semantic_match."""
    return _active_floor, _active_ceil


# ── Public entry point ────────────────────────────────────────────────────────

def compute_semantic_match(
    job: dict,
    profile: dict,
    persist: bool = True,
) -> tuple[int, float, str]:
    """
    Compute semantic similarity between the candidate resume and a JD.

    Returns:
        score   — int 0-25  (drops into the skill_match slot)
        raw_sim — float     (raw cosine value, stored for auditability)
        note    — str       (human-readable scoring note)

    Gracefully returns (0, 0.0, "sem_unavailable") if sentence-transformers
    is not installed, so callers can apply a keyword-score fallback (Issue #4).

    Args:
        persist: Forwarded to get_jd_embedding. scorer passes False to defer
                 SQLite persistence until the scoring bucket is known (Issue #2).
    """
    try:
        profile_version = profile.get("metadata", {}).get("profile_version", "unknown")
        resume_emb      = get_resume_embedding(profile)
        jd_emb          = get_jd_embedding(job, profile_version, persist=persist)
        sim             = cosine_similarity(resume_emb, jd_emb)
        # Use calibrated bounds set by set_calibration() (defaults to static constants).
        score           = sim_to_score(sim, _active_floor, _active_ceil)
        note            = f"sem_sim={sim:.3f} → {score}/25"
        return score, sim, note
    except (RuntimeError, ValueError, OSError) as exc:
        logger.warning("Semantic matching unavailable: %s", exc)
        return 0, 0.0, "sem_unavailable"


# ── Batch embedding (Issue #7) ─────────────────────────────────────────────────

def batch_embed_jobs(
    jobs: list[dict],
    profile_version: str,
    persist: bool = False,
) -> int:
    """
    Encode JD texts for all jobs not already in the in-memory cache in one
    model forward pass.

    Typical CPU speedup vs one-at-a-time: 3-8x for batches of 20+ jobs because
    the transformer processes all sequences in parallel rather than sequentially.

    Args:
        jobs:            list of job dicts (each needs 'id', 'title', 'description')
        profile_version: used as the in-memory cache key suffix
        persist:         if True, write newly computed embeddings to SQLite
                         (default False — scoring_agent defers to flush_jd_to_db)

    Returns the number of newly computed (not cache-hit) embeddings.
    """
    if not _NUMPY_AVAILABLE:
        return 0
    model = _load_model()
    if model is None:
        return 0

    # Collect jobs whose embeddings are not yet in memory
    uncached: list[tuple[int, dict]] = []
    for idx, job in enumerate(jobs):
        cache_key = (job.get("id"), profile_version)
        if cache_key not in _jd_mem_cache:
            uncached.append((idx, job))

    if not uncached:
        return 0

    texts      = [_jd_text(job) for _, job in uncached]
    embeddings = _embed(texts)   # single forward pass — (N, 384)

    for i, (_, job) in enumerate(uncached):
        emb       = embeddings[i].astype(np.float32)
        job_id    = job.get("id")
        cache_key = (job_id, profile_version)
        _jd_mem_cache[cache_key] = emb

        if persist and job_id is not None:
            from core import database
            database.upsert_jd_embedding(
                job_id          = job_id,
                profile_version = profile_version,
                model_name      = _MODEL_NAME,
                embedding_bytes = emb.tobytes(),
            )

    logger.info(
        "batch_embed_jobs: encoded %d JDs in one pass (skipped %d cached)",
        len(uncached), len(jobs) - len(uncached),
    )
    return len(uncached)


# ── Cache management ──────────────────────────────────────────────────────────

def clear_cache() -> None:
    """
    Flush in-memory JD cache, model singleton, and calibrated bounds.
    Call between test cases to ensure a clean slate.
    """
    global _jd_mem_cache, _model, _active_floor, _active_ceil
    _jd_mem_cache = {}
    _model        = None
    _active_floor = _SIM_FLOOR
    _active_ceil  = _SIM_CEIL


def invalidate_resume_cache(profile: dict) -> None:
    """
    Delete the on-disk resume embedding for this profile (forces recompute).

    Pass the full profile dict — the content hash is derived internally so the
    correct file is located even if profile_version was not changed (Issue #3).
    """
    content_hash = _profile_content_hash(profile)
    path         = _resume_disk_path(content_hash)
    if path.exists():
        path.unlink()
        logger.info("Invalidated resume embedding: %s", path.name)
