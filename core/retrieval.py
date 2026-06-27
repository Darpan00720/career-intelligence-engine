"""RAG retrieval over the candidate's own experience corpus (Option A).

The document agent used to dump the whole profile into the prompt. With RAG
enabled, we instead chunk the candidate's history (summary, experience bullets,
coursework, skills) into a small in-memory vector store and, for a given job,
retrieve the top-k most relevant chunks to *ground* the résumé / cover-letter
generation. Less generic output, less hallucination — the letter cites real,
relevant achievements matched to that job's requirements.

Reuses the existing embedding stack (core.semantic_matcher: SentenceTransformer
all-MiniLM-L6-v2 + cosine on L2-normalised vectors) — no new infrastructure.

The *retrieval* step needs no API credit (it runs the local embedding model), so
it can be demonstrated for free; only the downstream LLM generation costs credit.
Gracefully returns [] when numpy / sentence-transformers are unavailable.
"""
from __future__ import annotations

import os

from core import semantic_matcher as _sm

try:
    import numpy as _np
except ImportError:  # pragma: no cover - numpy is a hard dependency in practice
    _np = None  # type: ignore[assignment]

# profile_content_hash -> (chunks, embeddings ndarray). Avoids re-embedding the
# candidate's corpus for every job in a run.
_chunk_cache: dict[str, tuple[list[dict], object]] = {}


def is_enabled() -> bool:
    """RAG grounding is opt-in via ENABLE_RAG (default off)."""
    return os.getenv("ENABLE_RAG", "").strip().lower() in ("1", "true", "yes", "on")


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_profile(profile: dict) -> list[dict]:
    """Flatten the profile into retrievable {source, text} chunks."""
    chunks: list[dict] = []

    vp = profile.get("value_proposition")
    if vp:
        chunks.append({"source": "summary", "text": str(vp)})

    for exp in profile.get("experience", []) or []:
        role = exp.get("title") or exp.get("role") or ""
        company = exp.get("company") or exp.get("organization") or ""
        ctx = " — ".join(x for x in (role, company) if x)
        bullets = (exp.get("responsibilities") or exp.get("achievements")
                   or exp.get("bullets") or [])
        for bullet in bullets:
            text = str(bullet).strip()
            if text:
                chunks.append({"source": ctx or "experience",
                               "text": f"{ctx}: {text}" if ctx else text})
        if not bullets and ctx:
            summary = exp.get("summary") or exp.get("description") or ""
            chunks.append({"source": ctx, "text": f"{ctx}. {summary}".strip()})

    for edu in profile.get("education", []) or []:
        head = " ".join(x for x in (edu.get("degree", ""), edu.get("field", ""),
                                    "—", edu.get("institution", "")) if x).strip(" —")
        coursework = edu.get("key_coursework") or []
        if coursework:
            chunks.append({"source": "education",
                           "text": f"{head}. Coursework: {', '.join(map(str, coursework))}"})
        thesis = edu.get("thesis_or_project")
        if thesis:
            chunks.append({"source": "education", "text": f"{head}. {thesis}"})

    for group, items in (profile.get("skills") or {}).items():
        if isinstance(items, list):
            vals = [str(s) for s in items if not str(s).startswith("FILL_IN")]
            if vals:
                chunks.append({"source": f"skills/{group}",
                               "text": f"{group.replace('_', ' ')}: {', '.join(vals)}"})

    return [c for c in chunks if c["text"].strip()]


def _profile_embeddings(profile: dict):
    """(chunks, embeddings) for a profile, cached by content hash."""
    key = _sm._profile_content_hash(profile)
    if key in _chunk_cache:
        return _chunk_cache[key]
    chunks = _chunk_profile(profile)
    if not chunks:
        _chunk_cache[key] = (chunks, None)
        return chunks, None
    embeddings = _sm._embed([c["text"] for c in chunks])  # (N, 384), L2-normalised
    _chunk_cache[key] = (chunks, embeddings)
    return chunks, embeddings


# ── Retrieval ─────────────────────────────────────────────────────────────────

def retrieve(job: dict, profile: dict, k: int = 6) -> list[dict]:
    """Return the top-k profile chunks most relevant to this job.

    Each hit: {"source", "text", "score"} (score = cosine similarity, 0–1).
    Returns [] if embeddings are unavailable — callers fall back to the full profile.
    """
    if _np is None:
        return []
    try:
        chunks, embeddings = _profile_embeddings(profile)
        if not chunks or embeddings is None:
            return []
        jd_emb = _sm._embed([_sm._jd_text(job)])[0]
        sims = embeddings @ jd_emb           # normalised vectors → dot == cosine
        order = _np.argsort(-sims)[: max(0, k)]
        return [{"source": chunks[i]["source"], "text": chunks[i]["text"],
                 "score": float(sims[i])} for i in order]
    except (RuntimeError, ValueError, OSError):
        return []


def format_for_prompt(hits: list[dict]) -> str:
    """Render retrieved hits as a grounding block for the LLM prompt."""
    return "\n".join(f"- ({h['score']:.2f}) [{h['source']}] {h['text']}" for h in hits)


def clear_cache() -> None:
    """Drop the cached profile chunk embeddings (call between tests)."""
    _chunk_cache.clear()
