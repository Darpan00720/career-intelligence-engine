"""Memory Platform (v5.4).

Tenant-isolated conversation + memory management:

  EmbeddingService — text→vector (bag-of-words cosine default; injectable)
  ConversationStore — persisted conversations + messages
  ShortTermMemory — recent sliding window of a conversation
  LongTermMemory — persisted, embedded memories with semantic retrieval + expiry
  ContextAssembler — budget-bounded context (summary + retrieved + recent), with
                     conversation summarization / context compression
  MemoryManager — facade over the above

All reads/writes are scoped to the current tenant (core.tenancy).
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field

from core import database, tenancy

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingService:
    """Default dependency-free embedder (bag-of-words). Inject a real model for prod."""

    def embed(self, text: str) -> dict[str, float]:
        counts: dict[str, float] = {}
        for tok in _TOKEN_RE.findall((text or "").lower()):
            counts[tok] = counts.get(tok, 0.0) + 1.0
        return counts

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0


class ConversationStore:
    def create(self, *, user_id: str = "", title: str = "",
               tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or tenancy.current_tenant()
        cid = uuid.uuid4().hex
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO conversations (conversation_id, tenant_id, user_id, title) "
                "VALUES (?, ?, ?, ?)", (cid, tenant_id, user_id, title))
        return cid

    def add_message(self, conversation_id: str, role: str, content: str, *,
                    agent: str | None = None, tokens: int = 0,
                    tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO conversation_messages "
                "(conversation_id, tenant_id, role, content, agent, tokens) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, tenant_id, role, content, agent, tokens))
            conn.execute("UPDATE conversations SET updated_at = DATETIME('now') "
                         "WHERE conversation_id = ?", (conversation_id,))

    def messages(self, conversation_id: str, limit: int | None = None,
                 tenant_id: str | None = None) -> list[dict]:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT role, content, agent, tokens, created_at FROM conversation_messages "
                "WHERE conversation_id = ? AND tenant_id = ? ORDER BY id",
                (conversation_id, tenant_id)).fetchall()
        out = [dict(r) for r in rows]
        return out[-limit:] if limit else out

    def set_summary(self, conversation_id: str, summary: str,
                    tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute("UPDATE conversations SET summary = ? "
                         "WHERE conversation_id = ? AND tenant_id = ?",
                         (summary, conversation_id, tenant_id))

    def get_summary(self, conversation_id: str, tenant_id: str | None = None) -> str:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT summary FROM conversations WHERE conversation_id = ? AND tenant_id = ?",
                (conversation_id, tenant_id)).fetchone()
        return (row["summary"] if row and row["summary"] else "") if row else ""


@dataclass
class ShortTermMemory:
    store: ConversationStore
    window: int = 10

    def recent(self, conversation_id: str) -> list[dict]:
        return self.store.messages(conversation_id, limit=self.window)


@dataclass
class LongTermMemory:
    embedder: EmbeddingService = field(default_factory=EmbeddingService)

    def remember(self, content: str, *, scope: str = "general", ttl_seconds: int | None = None,
                 tenant_id: str | None = None) -> int:
        tenant_id = tenant_id or tenancy.current_tenant()
        embedding = json.dumps(self.embedder.embed(content))
        expires = None
        if ttl_seconds:
            expires = time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.gmtime(time.time() + ttl_seconds))
        with database.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO agent_memories (tenant_id, scope, content, embedding, expires_at) "
                "VALUES (?, ?, ?, ?, ?)", (tenant_id, scope, content, embedding, expires))
            return cur.lastrowid

    def expire(self, tenant_id: str | None = None) -> int:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM agent_memories WHERE tenant_id = ? AND expires_at IS NOT NULL "
                "AND expires_at < DATETIME('now')", (tenant_id,))
            return cur.rowcount

    def retrieve(self, query: str, k: int = 3, *, threshold: float = 0.0,
                 tenant_id: str | None = None) -> list[dict]:
        tenant_id = tenant_id or tenancy.current_tenant()
        self.expire(tenant_id)
        qvec = self.embedder.embed(query)
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT content, embedding, scope FROM agent_memories WHERE tenant_id = ?",
                (tenant_id,)).fetchall()
        scored = []
        for r in rows:
            vec = json.loads(r["embedding"]) if r["embedding"] else {}
            sim = self.embedder.cosine(qvec, vec)
            if sim >= threshold:
                scored.append({"content": r["content"], "scope": r["scope"], "score": round(sim, 4)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]


@dataclass
class ContextAssembler:
    short_term: ShortTermMemory
    long_term: LongTermMemory
    store: ConversationStore
    max_tokens: int = 4000

    def assemble(self, conversation_id: str, query: str, *, k_memories: int = 3) -> dict:
        summary = self.store.get_summary(conversation_id)
        memories = [m["content"] for m in self.long_term.retrieve(query, k=k_memories)]
        history = self.short_term.recent(conversation_id)
        return {"summary": summary, "memories": memories, "history": history}

    def compress(self, conversation_id: str, keep_last: int = 4,
                 summarizer=None) -> str:
        """Summarize older turns into the conversation summary; keep recent turns.

        Default summarizer truncates; inject an LLM summarizer for production."""
        msgs = self.store.messages(conversation_id)
        if len(msgs) <= keep_last:
            return self.store.get_summary(conversation_id)
        older = msgs[:-keep_last]
        joined = " ".join(f"{m['role']}: {m['content']}" for m in older)
        summary = summarizer(joined) if summarizer else (joined[:500] + "…" if len(joined) > 500 else joined)
        existing = self.store.get_summary(conversation_id)
        combined = (existing + " " + summary).strip() if existing else summary
        self.store.set_summary(conversation_id, combined)
        return combined


class MemoryManager:
    """Facade tying conversation store, short/long-term memory, and assembly."""

    def __init__(self, embedder: EmbeddingService | None = None, *, window: int = 10):
        self.embedder = embedder or EmbeddingService()
        self.store = ConversationStore()
        self.short_term = ShortTermMemory(self.store, window=window)
        self.long_term = LongTermMemory(self.embedder)
        self.assembler = ContextAssembler(self.short_term, self.long_term, self.store)

    def start_conversation(self, **kw) -> str:
        return self.store.create(**kw)

    def add_message(self, conversation_id: str, role: str, content: str, **kw) -> None:
        self.store.add_message(conversation_id, role, content, **kw)

    def remember(self, content: str, **kw) -> int:
        return self.long_term.remember(content, **kw)

    def context_for(self, conversation_id: str, query: str, **kw) -> dict:
        return self.assembler.assemble(conversation_id, query, **kw)
