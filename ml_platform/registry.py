"""Model Registry (v5.1) — versioned models with metadata, lineage, and stages.

Stages: staging → production → archived. Stores the trained artifact (e.g.
learning-to-rank weights) plus metadata and training lineage for reproducibility.
"""
from __future__ import annotations

import json

from core import database

STAGING = "staging"
PRODUCTION = "production"
ARCHIVED = "archived"


class ModelRegistry:
    def register(self, name: str, artifact: dict, metadata: dict | None = None,
                 lineage: dict | None = None) -> int:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM ml_models WHERE name = ?", (name,)
            ).fetchone()
            version = (row["v"] or 0) + 1
            conn.execute(
                "INSERT INTO ml_models (name, version, stage, metadata, lineage, artifact) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, version, STAGING, json.dumps(metadata or {}),
                 json.dumps(lineage or {}), json.dumps(artifact)),
            )
        return version

    def _row(self, row) -> dict | None:
        if not row:
            return None
        return {"name": row["name"], "version": row["version"], "stage": row["stage"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "lineage": json.loads(row["lineage"]) if row["lineage"] else {},
                "artifact": json.loads(row["artifact"]) if row["artifact"] else {}}

    def get(self, name: str, version: int | None = None) -> dict | None:
        with database.get_connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM ml_models WHERE name = ? ORDER BY version DESC LIMIT 1",
                    (name,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM ml_models WHERE name = ? AND version = ?",
                    (name, version)).fetchone()
        return self._row(row)

    def production_model(self, name: str) -> dict | None:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM ml_models WHERE name = ? AND stage = ? "
                "ORDER BY version DESC LIMIT 1", (name, PRODUCTION)).fetchone()
        return self._row(row)

    def promote(self, name: str, version: int, stage: str = PRODUCTION) -> None:
        if stage not in (STAGING, PRODUCTION, ARCHIVED):
            raise ValueError(f"unknown stage: {stage}")
        with database.get_connection() as conn:
            if stage == PRODUCTION:
                # Demote any current production model to archived (one prod per name).
                conn.execute(
                    "UPDATE ml_models SET stage = ? WHERE name = ? AND stage = ?",
                    (ARCHIVED, name, PRODUCTION))
            conn.execute(
                "UPDATE ml_models SET stage = ? WHERE name = ? AND version = ?",
                (stage, name, version))

    def list_models(self, name: str) -> list[dict]:
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ml_models WHERE name = ? ORDER BY version", (name,)
            ).fetchall()
        return [self._row(r) for r in rows]
