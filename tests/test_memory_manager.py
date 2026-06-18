"""Memory platform tests — conversation store, semantic memory, isolation."""
import unittest

from tests._agent_support import MemDB
from core import tenancy
from core.conversation_manager import ConversationManager
from core.memory_manager import EmbeddingService, MemoryManager


class TestEmbedding(unittest.TestCase):
    def test_cosine(self):
        e = EmbeddingService()
        self.assertGreater(e.cosine(e.embed("ai product manager"),
                                    e.embed("product manager ai")), 0.9)
        self.assertEqual(e.cosine(e.embed("ai"), e.embed("")), 0.0)


class TestMemory(MemDB):
    def test_conversation_roundtrip(self):
        mm = MemoryManager()
        cid = mm.start_conversation(user_id="u1", title="t")
        mm.add_message(cid, "user", "hello")
        mm.add_message(cid, "assistant", "hi", agent="planner")
        self.assertEqual([m["role"] for m in mm.store.messages(cid)], ["user", "assistant"])

    def test_long_term_semantic_retrieval(self):
        mm = MemoryManager()
        mm.remember("Candidate targets AI Product Management roles", scope="profile")
        mm.remember("Prefers Berlin and remote", scope="profile")
        hits = mm.long_term.retrieve("AI product management", k=1)
        self.assertEqual(len(hits), 1)
        self.assertIn("Product Management", hits[0]["content"])

    def test_memory_expiry(self):
        mm = MemoryManager()
        mm.remember("ephemeral", ttl_seconds=-1)
        self.assertEqual(mm.long_term.retrieve("ephemeral"), [])

    def test_tenant_isolation(self):
        mm = MemoryManager()
        with tenancy.use_tenant("acme"):
            mm.remember("acme secret plan")
        with tenancy.use_tenant("beta"):
            self.assertEqual(mm.long_term.retrieve("plan"), [])

    def test_conversation_compression(self):
        cm = ConversationManager(compress_after=5)
        cid = cm.start()
        for i in range(8):
            cm.user_says(cid, f"message {i}")
        self.assertTrue(cm.memory.store.get_summary(cid))

    def test_load_many_memories(self):
        mm = MemoryManager()
        for i in range(500):
            mm.remember(f"memory item number {i} about topic {i % 10}", scope="bulk")
        self.assertEqual(len(mm.long_term.retrieve("topic 3 item", k=5)), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
