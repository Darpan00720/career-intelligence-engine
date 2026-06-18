"""Async runtime (v5.2) — non-blocking persistence layer.

Provides async counterparts of the v5.1 persistence port:
AsyncDatabaseManager / AsyncConnection / AsyncRepository / AsyncUnitOfWork /
AsyncTenantAwareRepository.

The default connector wraps SQLite via ``asyncio.to_thread`` so blocking DB
calls run off the event loop (the loop never blocks). In production the same
ports are backed by ``asyncpg`` / ``aiosqlite`` connection pools — inject a
different ``connect_fn`` and nothing else changes. Supports graceful
cancellation and timeout propagation through standard asyncio mechanisms.
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Awaitable, Callable

from core import config
from core.persistence import POSTGRES, SQLITE, StaleDataError


class AsyncConnection:
    """Async facade over a DB connection; SQLite calls run via to_thread."""

    def __init__(self, sync_conn):
        self._c = sync_conn

    async def execute(self, sql: str, params: tuple = ()) -> dict:
        def _run():
            cur = self._c.execute(sql, params)
            return {"lastrowid": cur.lastrowid, "rowcount": cur.rowcount}
        return await asyncio.to_thread(_run)

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        def _run():
            row = self._c.execute(sql, params).fetchone()
            return dict(row) if row is not None else None
        return await asyncio.to_thread(_run)

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        def _run():
            return [dict(r) for r in self._c.execute(sql, params).fetchall()]
        return await asyncio.to_thread(_run)

    async def commit(self) -> None:
        await asyncio.to_thread(self._c.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._c.rollback)

    async def close(self) -> None:
        await asyncio.to_thread(self._c.close)


class AsyncDatabaseManager:
    def __init__(self, dialect: str = SQLITE,
                 connect_fn: Callable[[], Awaitable[AsyncConnection]] | None = None):
        self.dialect = dialect
        self._connect_fn = connect_fn or self._default_connect

    async def _default_connect(self) -> AsyncConnection:
        def _open():
            from pathlib import Path
            Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: the connection is driven from to_thread
            # worker threads; access is serialized by awaiting sequentially.
            conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        return AsyncConnection(await asyncio.to_thread(_open))

    async def connect(self) -> AsyncConnection:
        return await self._connect_fn()

    @property
    def json_type(self) -> str:
        return "JSONB" if self.dialect == POSTGRES else "TEXT"


class AsyncUnitOfWork:
    def __init__(self, manager: AsyncDatabaseManager | None = None):
        self.manager = manager or get_async_db_manager()
        self.conn: AsyncConnection | None = None

    async def __aenter__(self) -> "AsyncUnitOfWork":
        self.conn = await self.manager.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                await self.conn.commit()
            else:
                await self.conn.rollback()
        finally:
            await self.conn.close()

    def repository(self, repo_cls, *args, **kwargs):
        return repo_cls(self.conn, *args, **kwargs)


class AsyncRepository:
    def __init__(self, conn: AsyncConnection, table: str):
        self.conn = conn
        self.table = table

    async def add(self, data: dict) -> int:
        cols = ", ".join(data)
        ph = ", ".join("?" for _ in data)
        res = await self.conn.execute(
            f"INSERT INTO {self.table} ({cols}) VALUES ({ph})", tuple(data.values()))
        return res["lastrowid"]

    async def get(self, entity_id: int, id_col: str = "id") -> dict | None:
        return await self.conn.fetchone(
            f"SELECT * FROM {self.table} WHERE {id_col} = ?", (entity_id,))

    async def list(self, where: dict | None = None, limit: int = 1000) -> list[dict]:
        where = where or {}
        clause = " AND ".join(f"{k} = ?" for k in where)
        sql = f"SELECT * FROM {self.table}"
        if clause:
            sql += f" WHERE {clause}"
        sql += " LIMIT ?"
        return await self.conn.fetchall(sql, (*where.values(), limit))

    async def update(self, entity_id: int, data: dict, *, expected_version: int | None = None,
                     id_col: str = "id") -> None:
        sets = ", ".join(f"{k} = ?" for k in data)
        params = list(data.values())
        if expected_version is not None:
            sets += ", version = version + 1"
            sql = f"UPDATE {self.table} SET {sets} WHERE {id_col} = ? AND version = ?"
            params += [entity_id, expected_version]
        else:
            sql = f"UPDATE {self.table} SET {sets} WHERE {id_col} = ?"
            params += [entity_id]
        res = await self.conn.execute(sql, tuple(params))
        if expected_version is not None and res["rowcount"] == 0:
            raise StaleDataError(
                f"{self.table}#{entity_id} changed underneath version {expected_version}")


class AsyncTenantAwareRepository(AsyncRepository):
    def __init__(self, conn: AsyncConnection, table: str, tenant_id: str | None = None):
        super().__init__(conn, table)
        from core.tenancy import current_tenant
        self.tenant_id = tenant_id or current_tenant()

    async def add(self, data: dict) -> int:
        return await super().add({**data, "tenant_id": self.tenant_id})

    async def get(self, entity_id: int, id_col: str = "id") -> dict | None:
        return await self.conn.fetchone(
            f"SELECT * FROM {self.table} WHERE {id_col} = ? AND tenant_id = ?",
            (entity_id, self.tenant_id))

    async def list(self, where: dict | None = None, limit: int = 1000) -> list[dict]:
        return await super().list({**(where or {}), "tenant_id": self.tenant_id}, limit)


_async_manager: AsyncDatabaseManager | None = None


def get_async_db_manager() -> AsyncDatabaseManager:
    global _async_manager
    if _async_manager is None:
        import os
        _async_manager = AsyncDatabaseManager(dialect=os.getenv("DB_DIALECT", SQLITE))
    return _async_manager


def set_async_db_manager(manager: AsyncDatabaseManager) -> None:
    global _async_manager
    _async_manager = manager
