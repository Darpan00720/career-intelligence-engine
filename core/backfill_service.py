"""Partition backfill & verification (v5.2.1).

Moves historical rows into partitioned tables in bounded keyset batches (low,
constant memory), then verifies the move by row count and content checksum.
Supports rollback. Storage-agnostic over a DB-API connection exposing
`execute(sql, params) -> cursor`; works on SQLite (unit-tested with real copies)
and psycopg/PostgreSQL (same SQL, `%s` placeholders).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class PartitionBackfillService:
    placeholder: str = "?"          # "?" for sqlite, "%s" for psycopg
    id_col: str = "id"
    batch_size: int = 1000

    def _ph(self, n: int = 1) -> str:
        return ", ".join([self.placeholder] * n)

    # ── Counts / checksums ──────────────────────────────────────────────────────
    def row_count(self, conn, table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def checksum(self, conn, table: str, columns: list[str] | None = None) -> str:
        """Deterministic content hash, computed in keyset batches (bounded memory)."""
        cols = columns or ["*"]
        select_cols = f"{self.id_col}, " + ", ".join(cols) if cols != ["*"] else "*"
        h = hashlib.md5()
        last, ph = 0, self.placeholder
        while True:
            rows = conn.execute(
                f"SELECT {select_cols} FROM {table} WHERE {self.id_col} > {ph} "
                f"ORDER BY {self.id_col} LIMIT {ph}", (last, self.batch_size)
            ).fetchall()
            if not rows:
                break
            for r in rows:
                h.update(repr(tuple(r)).encode())
                last = r[0]
        return h.hexdigest()

    # ── Backfill ──────────────────────────────────────────────────────────────
    def backfill(self, conn, source: str, target: str,
                 columns: list[str], where: str | None = None) -> int:
        """Copy rows source→target in keyset batches. Returns rows copied."""
        col_list = ", ".join(columns)
        ph = self.placeholder
        copied, last = 0, 0
        cond = f" AND ({where})" if where else ""
        while True:
            rows = conn.execute(
                f"SELECT {self.id_col}, {col_list} FROM {source} "
                f"WHERE {self.id_col} > {ph}{cond} ORDER BY {self.id_col} LIMIT {ph}",
                (last, self.batch_size),
            ).fetchall()
            if not rows:
                break
            insert_cols = f"{self.id_col}, {col_list}"
            values_ph = self._ph(len(columns) + 1)
            conn.executemany(
                f"INSERT INTO {target} ({insert_cols}) VALUES ({values_ph})",
                [tuple(r) for r in rows],
            )
            copied += len(rows)
            last = rows[-1][0]
        return copied

    # ── Verification ─────────────────────────────────────────────────────────────
    def verify_counts(self, conn, source: str, target: str) -> dict:
        s, t = self.row_count(conn, source), self.row_count(conn, target)
        return {"source": s, "target": t, "match": s == t}

    def verify_checksum(self, conn, source: str, target: str,
                        columns: list[str]) -> dict:
        s = self.checksum(conn, source, columns)
        t = self.checksum(conn, target, columns)
        return {"source_checksum": s, "target_checksum": t, "match": s == t}

    def verify(self, conn, source: str, target: str, columns: list[str]) -> dict:
        counts = self.verify_counts(conn, source, target)
        checks = self.verify_checksum(conn, source, target, columns)
        return {"counts": counts, "checksum": checks,
                "ok": counts["match"] and checks["match"]}

    # ── Rollback ──────────────────────────────────────────────────────────────
    def rollback(self, conn, target: str) -> None:
        """Undo a backfill by clearing the target table."""
        conn.execute(f"DELETE FROM {target}")
