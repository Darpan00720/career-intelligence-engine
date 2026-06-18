"""Agent Registry (v5.4).

Catalog of agents available to the runtime: each has a name, a system prompt,
declared capabilities (for capability-based routing / handoffs), and the tools
it is allowed to call. The coordinator uses `find_by_capability` to delegate and
hand off between agents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class AgentSpec:
    name: str
    system_prompt: str = ""
    capabilities: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    model_task: str = "research"          # routing hint for the LLMRouter
    handler: Callable | None = None       # optional custom turn handler(ctx) -> dict


class AgentRegistry:
    def __init__(self):
        self._agents: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> AgentSpec:
        self._agents[spec.name] = spec
        return spec

    def get(self, name: str) -> AgentSpec:
        if name not in self._agents:
            raise KeyError(f"unknown agent: {name}")
        return self._agents[name]

    def list(self) -> list[str]:
        return sorted(self._agents)

    def find_by_capability(self, capability: str) -> list[AgentSpec]:
        return [s for s in self._agents.values() if capability in s.capabilities]


_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def reset_agent_registry() -> None:
    global _registry
    _registry = None
