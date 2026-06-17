"""State reducers for CareerState.

LangGraph applies the reducer attached to an Annotated state field to merge
each node's partial update into the running state. These functions are plain,
import-free, and independently testable — no langgraph import required.

Reducer contract: f(existing, update) -> merged. Both args may be None.
"""
from __future__ import annotations

from typing import Any


def extend(existing: list | None, update: list | None) -> list:
    """Append update onto existing (used for errors, audit_log)."""
    base = list(existing) if existing else []
    if update:
        base.extend(update)
    return base


def _key(item: Any) -> Any:
    """Identity key for upsert: prefer .job_id attr, then ['job_id']."""
    if hasattr(item, "job_id"):
        return item.job_id
    if isinstance(item, dict) and "job_id" in item:
        return item["job_id"]
    return id(item)


def merge_by_job_id(existing: list | None, update: list | None) -> list:
    """Upsert items by job_id: update replaces same-id, appends new (stable order)."""
    out: list = list(existing) if existing else []
    if not update:
        return out
    index = {_key(item): i for i, item in enumerate(out)}
    for item in update:
        k = _key(item)
        if k in index:
            out[index[k]] = item
        else:
            index[k] = len(out)
            out.append(item)
    return out


def merge_dict(existing: dict | None, update: dict | None) -> dict:
    """Shallow dict merge (used for retry_count, human_decisions)."""
    out = dict(existing) if existing else {}
    if update:
        out.update(update)
    return out


def take_latest(existing: Any, update: Any) -> Any:
    """Last-write-wins, ignoring None updates (explicit replace reducer)."""
    return update if update is not None else existing
