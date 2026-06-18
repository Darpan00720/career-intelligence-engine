"""Agent Orchestrator (v4).

Registers agents and executes a workflow expressed as ordered *stages*. Each
stage is a list of agent names; agents within a stage may run in parallel.
Collects per-agent metrics, retries failed agents, and tracks dependencies via
stage ordering.

    orch = Orchestrator(max_retries=1)
    for agent in build_agents(context).values():
        orch.register(agent)
    report = orch.run(
        [["search"], ["scoring"], ["research", "analytics"], ["documents"],
         ["recommendation"], ["tracker"]],
        mode="parallel",
    )

Modes: "sequential" (one agent at a time) or "parallel" (agents in the same
stage run concurrently). Stages always run in order, preserving dependencies.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from agents.base_agent import AgentResult, BaseAgent
from core.event_log import log_event

SEQUENTIAL = "sequential"
PARALLEL = "parallel"


@dataclass
class OrchestrationReport:
    results: list[AgentResult] = field(default_factory=list)
    mode: str = SEQUENTIAL

    @property
    def failures(self) -> list[str]:
        return [r.name for r in self.results if r.status == "error"]

    @property
    def total_duration(self) -> float:
        return round(sum(r.duration for r in self.results), 3)

    @property
    def succeeded(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "agents": [asdict(r) for r in self.results],
            "failures": self.failures,
            "total_duration": self.total_duration,
            "succeeded": self.succeeded,
        }


class Orchestrator:
    def __init__(self, max_retries: int = 1, max_workers: int = 4):
        self.max_retries = max_retries
        self.max_workers = max_workers
        self.agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> "Orchestrator":
        self.agents[agent.name] = agent
        return self

    def register_all(self, agents) -> "Orchestrator":
        for agent in (agents.values() if isinstance(agents, dict) else agents):
            self.register(agent)
        return self

    # ── Execution ────────────────────────────────────────────────────────────
    def _run_with_retry(self, name: str) -> AgentResult:
        agent = self.agents.get(name)
        if agent is None:
            return AgentResult(name, "error", error=f"agent not registered: {name}")
        result = agent.execute()
        attempt = 0
        while result.status == "error" and attempt < self.max_retries:
            attempt += 1
            log_event("pipeline", "agent_retry", status="retry",
                      error=f"{name} attempt {attempt}")
            result = agent.execute()
        return result

    def run(self, stages: list[list[str]], mode: str = SEQUENTIAL) -> OrchestrationReport:
        """Execute the workflow stages in order. Returns an OrchestrationReport."""
        report = OrchestrationReport(mode=mode)
        for stage in stages:
            if mode == PARALLEL and len(stage) > 1:
                with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    # Preserve stage order in the report for determinism.
                    for res in pool.map(self._run_with_retry, stage):
                        report.results.append(res)
            else:
                for name in stage:
                    report.results.append(self._run_with_retry(name))
        log_event("pipeline", "orchestration_complete", status=
                  "ok" if report.succeeded else "partial",
                  duration=report.total_duration)
        return report
