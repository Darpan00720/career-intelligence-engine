"""Test support: leak-free SqliteSaver factory (Phase 8A / R3).

Tracks every checkpoint connection opened during a test so it can be closed in
tearDown, eliminating sqlite ResourceWarnings. Production code is unaffected.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from graph.checkpoint import _career_serde

_OPEN_CONNS: list[sqlite3.Connection] = []


def make_saver(path) -> SqliteSaver:
    conn = sqlite3.connect(str(Path(path)), check_same_thread=False)
    saver = SqliteSaver(conn, serde=_career_serde())
    saver.setup()
    _OPEN_CONNS.append(conn)
    return saver


def close_all() -> None:
    while _OPEN_CONNS:
        try:
            _OPEN_CONNS.pop().close()
        except Exception:
            pass
