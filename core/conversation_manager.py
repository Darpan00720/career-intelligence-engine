"""Conversation Manager (v5.4).

Long-running conversation lifecycle over the Memory Platform: create
conversations, append user/assistant turns, retrieve assembled context for the
next agent turn, and auto-compress (summarize) older history when it grows past
a threshold. Tenant-isolated via the underlying MemoryManager.
"""
from __future__ import annotations

from core.memory_manager import MemoryManager


class ConversationManager:
    def __init__(self, memory: MemoryManager | None = None, *,
                 compress_after: int = 20, summarizer=None):
        self.memory = memory or MemoryManager()
        self.compress_after = compress_after
        self.summarizer = summarizer

    def start(self, *, user_id: str = "", title: str = "") -> str:
        return self.memory.start_conversation(user_id=user_id, title=title)

    def user_says(self, conversation_id: str, text: str) -> None:
        self.memory.add_message(conversation_id, "user", text)
        self._maybe_compress(conversation_id)

    def assistant_says(self, conversation_id: str, text: str, *, agent: str | None = None,
                       tokens: int = 0) -> None:
        self.memory.add_message(conversation_id, "assistant", text, agent=agent, tokens=tokens)
        self._maybe_compress(conversation_id)

    def history(self, conversation_id: str, limit: int | None = None) -> list[dict]:
        return self.memory.store.messages(conversation_id, limit=limit)

    def context(self, conversation_id: str, query: str, **kw) -> dict:
        return self.memory.context_for(conversation_id, query, **kw)

    def _maybe_compress(self, conversation_id: str) -> None:
        msgs = self.memory.store.messages(conversation_id)
        if len(msgs) > self.compress_after:
            self.memory.assembler.compress(conversation_id, keep_last=6,
                                           summarizer=self.summarizer)
