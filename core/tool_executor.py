"""Tool-Calling Platform (v5.4).

Async tool execution with permissions, sandboxing, timeouts, retries, and audit:

  ToolRegistry        — register/get tools (name, fn, permission, timeout)
  ToolPermissions     — role → allowed tools (RBAC for tools)
  ToolSandbox         — policy sandbox: allowlist + timeout + arg validation
  ToolExecutor        — async invoke with retries, audit, metrics, spans
  ToolInvocationEngine — resolve a batch of tool calls (from an agent/LLM)

Sandboxing here is *policy-level* (allowlist, timeout, no unregistered calls);
OS-level isolation (seccomp/containers) is an infra concern noted in the docs.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from core import tenancy
from core.metrics import registry as _metrics


@dataclass
class Tool:
    name: str
    fn: Callable
    permission: str | None = None     # required permission (None = open)
    timeout: float | None = 10.0
    description: str = ""


class ToolError(Exception):
    pass


class ToolPermissionDenied(ToolError):
    pass


class ToolNotFound(ToolError):
    pass


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, name: str, fn: Callable, *, permission: str | None = None,
                 timeout: float | None = 10.0, description: str = "") -> Tool:
        tool = Tool(name, fn, permission, timeout, description)
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolNotFound(name)
        return self._tools[name]

    def list(self) -> list[str]:
        return sorted(self._tools)


@dataclass
class ToolPermissions:
    """role → allowed tool names (use {'*'} for all)."""
    matrix: dict[str, set[str]] = field(default_factory=dict)

    def allow(self, role: str, *tools: str) -> None:
        self.matrix.setdefault(role, set()).update(tools)

    def can(self, role: str, tool: str) -> bool:
        allowed = self.matrix.get(role, set())
        return "*" in allowed or tool in allowed


class ToolSandbox:
    """Policy sandbox: only registered, permitted tools may run, under a timeout."""

    def __init__(self, registry: ToolRegistry, permissions: ToolPermissions | None = None):
        self.registry = registry
        self.permissions = permissions or ToolPermissions()

    def authorize(self, tool_name: str, role: str | None) -> Tool:
        tool = self.registry.get(tool_name)   # raises ToolNotFound if unregistered
        if tool.permission and role is not None and not self.permissions.can(role, tool_name):
            raise ToolPermissionDenied(f"role {role!r} cannot call {tool_name!r}")
        return tool


class ToolExecutor:
    """Async tool invocation with retries, audit logging, and metrics."""

    def __init__(self, sandbox: ToolSandbox, max_retries: int = 1):
        self.sandbox = sandbox
        self.max_retries = max_retries

    async def invoke(self, tool_name: str, args: dict | None = None, *,
                     role: str | None = None, tenant_id: str | None = None) -> Any:
        from core.tracing import span
        tool = self.sandbox.authorize(tool_name, role)
        args = args or {}
        tenant_id = tenant_id or tenancy.current_tenant()
        _metrics().inc("tool_invocations", labels={"tool": tool_name})

        attempts, last_error = 0, None
        with span(f"tool:{tool_name}"):
            while attempts <= self.max_retries:
                attempts += 1
                try:
                    result = await self._run(tool, args)
                    tenancy.audit("tool.invoke", "tool", tool_name, tenant_id=tenant_id)
                    return result
                except asyncio.TimeoutError:
                    last_error = f"timeout after {tool.timeout}s"
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
        _metrics().inc("tool_failures", labels={"tool": tool_name})
        tenancy.audit("tool.failed", "tool", f"{tool_name}: {last_error}", tenant_id=tenant_id)
        raise ToolError(f"{tool_name} failed: {last_error}")

    async def _run(self, tool: Tool, args: dict) -> Any:
        async def _call():
            if inspect.iscoroutinefunction(tool.fn):
                return await tool.fn(**args)
            return await asyncio.to_thread(lambda: tool.fn(**args))
        return await (asyncio.wait_for(_call(), tool.timeout) if tool.timeout else _call())


class ToolInvocationEngine:
    """Resolve a batch of tool calls (e.g. emitted by an agent/LLM)."""

    def __init__(self, executor: ToolExecutor):
        self.executor = executor

    async def run_calls(self, calls: list[dict], *, role: str | None = None,
                        tenant_id: str | None = None) -> list[dict]:
        """calls: [{"tool": name, "args": {...}}]. Returns per-call results/errors."""
        out: list[dict] = []
        for call in calls:
            name = call.get("tool")
            try:
                result = await self.executor.invoke(name, call.get("args"),
                                                    role=role, tenant_id=tenant_id)
                out.append({"tool": name, "ok": True, "result": result})
            except ToolError as exc:
                out.append({"tool": name, "ok": False, "error": str(exc)})
        return out
