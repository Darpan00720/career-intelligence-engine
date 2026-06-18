"""Tool platform tests — permissions, sandbox, timeout, retries, async, audit."""
import asyncio
import unittest

from tests._agent_support import MemDB
from core.tool_executor import (
    ToolError,
    ToolExecutor,
    ToolInvocationEngine,
    ToolPermissionDenied,
    ToolPermissions,
    ToolRegistry,
    ToolSandbox,
)


class TestToolExecutor(MemDB, unittest.IsolatedAsyncioTestCase):
    def _engine(self):
        reg = ToolRegistry()
        reg.register("search", lambda q: f"results:{q}", permission="tools:search")
        reg.register("open", lambda: "ok")
        perms = ToolPermissions(); perms.allow("RECRUITER", "search")
        return ToolInvocationEngine(ToolExecutor(ToolSandbox(reg, perms)))

    async def test_invoke_with_permission(self):
        out = await self._engine().executor.invoke("search", {"q": "ai"}, role="RECRUITER")
        self.assertEqual(out, "results:ai")

    async def test_permission_denied(self):
        with self.assertRaises(ToolPermissionDenied):
            await self._engine().executor.invoke("search", {"q": "ai"}, role="USER")

    async def test_unregistered_tool(self):
        with self.assertRaises(ToolError):
            await self._engine().executor.invoke("ghost", {})

    async def test_timeout(self):
        reg = ToolRegistry()
        async def slow():
            await asyncio.sleep(0.5)
        reg.register("slow", slow, timeout=0.05)
        ex = ToolExecutor(ToolSandbox(reg), max_retries=0)
        with self.assertRaises(ToolError):
            await ex.invoke("slow", {})

    async def test_retry_then_succeed(self):
        state = {"n": 0}
        def flaky():
            state["n"] += 1
            if state["n"] < 2:
                raise RuntimeError("again")
            return "ok"
        reg = ToolRegistry(); reg.register("flaky", flaky)
        ex = ToolExecutor(ToolSandbox(reg), max_retries=2)
        self.assertEqual(await ex.invoke("flaky", {}), "ok")

    async def test_batch_run_calls(self):
        out = await self._engine().run_calls(
            [{"tool": "open", "args": {}}, {"tool": "ghost", "args": {}}], role="RECRUITER")
        self.assertTrue(out[0]["ok"])
        self.assertFalse(out[1]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
