"""Feature Store (v5.1) — versioned, persisted feature sets.

Stores named feature-set versions (schema + rows) so training and offline
evaluation are reproducible. Versions are immutable and auto-incremented.
"""
from __future__ import annotations

import json

from core import database


class FeatureStore:
    def register(self, name: str, features: list[str], data: list[dict]) -> int:
        """Persist a new immutable version of a feature set. Returns the version."""
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM ml_feature_sets WHERE name = ?", (name,)
            ).fetchone()
            version = (row["v"] or 0) + 1
            conn.execute(
                "INSERT INTO ml_feature_sets (name, version, features, data) "
                "VALUES (?, ?, ?, ?)",
                (name, version, json.dumps(features), json.dumps(data)),
            )
        return version

    def get(self, name: str, version: int | None = None) -> dict | None:
        with database.get_connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM ml_feature_sets WHERE name = ? "
                    "ORDER BY version DESC LIMIT 1", (name,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM ml_feature_sets WHERE name = ? AND version = ?",
                    (name, version),
                ).fetchone()
        if not row:
            return None
        return {"name": row["name"], "version": row["version"],
                "features": json.loads(row["features"]) if row["features"] else [],
                "data": json.loads(row["data"]) if row["data"] else []}

    def versions(self, name: str) -> list[int]:
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT version FROM ml_feature_sets WHERE name = ? ORDER BY version",
                (name,)
            ).fetchall()
        return [r["version"] for r in rows]

    def materialize(self, name: str, version: int | None = None) -> list[dict]:
        fs = self.get(name, version)
        return fs["data"] if fs else []
