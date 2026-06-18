"""Agent Runtime (v5.4).

Multi-agent execution with delegation, handoffs, checkpointing, recovery, and
interruption:

  AgentExecutor    — runs one agent turn (assemble context → LLM → optional tools)
  AgentCoordinator — delegation, capability-based handoff, sequential plans
  AgentRuntime     — runs a plan with per-step checkpoints persisted to
                     `agent_runs`, resume-after-failure, and interruptible cancel

Deterministic and testable: with the LocalProvider the LLMRouter returns stable
text, and agent `handler`s can inject behavior (delegation, tool calls). State is
persisted as JSON so a crashed run resumes from its last completed step.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import asdict, dataclass, field

from core import database, tenancy
from core.agent_registry import AgentRegistry, AgentSpec, get_agent_registry
from core.llm_router import ContextBuilder, LLMRouter
from core.metrics import registry as _metrics

PENDING, RUNNING, COMPLETED, FAILED, CANCELLED = (
    "PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")


@dataclass
class AgentResult:
    agent: str
    text: str = ""
    tokens: int = 0
    cost: float = 0.0
    handoff_to: str | None = None
    tool_results: list = field(default_factory=list)


class AgentExecutor:
    def __init__(self, llm_router: LLMRouter | None = None, *, memory=None,
                 tools=None, context_builder: ContextBuilder | None = None):
        self.llm = llm_router or LLMRouter()
        self.memory = memory
        self.tools = tools                # ToolInvocationEngine (optional)
        self.context_builder = context_builder or ContextBuilder()

    def run_turn(self, spec: AgentSpec, *, conversation_id: str | None = None,
                 user_input: str = "", tenant_id: str | None = None,
                 role: str | None = None) -> AgentResult:
        import time
        tenant_id = tenant_id or tenancy.current_tenant()
        ctx = (self.memory.context_for(conversation_id, user_input)
               if (self.memory and conversation_id) else {})
        prompt = self.context_builder.build(
            system=spec.system_prompt, history=ctx.get("history"),
            memory=ctx.get("memories"), user=user_input)

        _metrics().inc("agent_active", labels={"agent": spec.name})
        start = time.perf_counter()

        if spec.handler is not None:
            out = spec.handler({"spec": spec, "prompt": prompt, "user": user_input,
                                "context": ctx, "tenant_id": tenant_id}) or {}
            text = out.get("text", "")
            result = AgentResult(spec.name, text, out.get("tokens", 0),
                                 out.get("cost", 0.0), out.get("handoff_to"))
            tool_calls = out.get("tool_calls", [])
        else:
            llm_result = self.llm.complete(prompt, task=spec.model_task, tenant_id=tenant_id)
            result = AgentResult(spec.name, llm_result.text, llm_result.total_tokens,
                                 llm_result.cost_usd)
            tool_calls = []

        if tool_calls and self.tools is not None:
            result.tool_results = self._run_tools(tool_calls, role, tenant_id)

        _metrics().observe("agent_execution_time", time.perf_counter() - start,
                           {"agent": spec.name})
        if self.memory and conversation_id and result.text:
            self.memory.add_message(conversation_id, "assistant", result.text,
                                    agent=spec.name, tokens=result.tokens)
        return result

    def _run_tools(self, tool_calls, role, tenant_id) -> list:
        # Bridge the sync turn to the async tool engine. Only valid when not
        # already inside an event loop (use the async path for that).
        try:
            asyncio.get_running_loop()
            inside_loop = True
        except RuntimeError:
            inside_loop = False
        if inside_loop:
            raise RuntimeError("use the async tool path when inside a running event loop")
        return asyncio.run(self.tools.run_calls(tool_calls, role=role, tenant_id=tenant_id))


class AgentCoordinator:
    def __init__(self, executor: AgentExecutor | None = None,
                 registry: AgentRegistry | None = None):
        self.executor = executor or AgentExecutor()
        self.registry = registry or get_agent_registry()

    def delegate(self, to_agent: str, *, conversation_id: str | None = None,
                 task: str = "", tenant_id: str | None = None) -> AgentResult:
        spec = self.registry.get(to_agent)
        return self.executor.run_turn(spec, conversation_id=conversation_id,
                                      user_input=task, tenant_id=tenant_id)

    def handoff(self, capability: str, *, conversation_id: str | None = None,
                user_input: str = "", tenant_id: str | None = None) -> AgentResult:
        candidates = self.registry.find_by_capability(capability)
        if not candidates:
            raise KeyError(f"no agent has capability {capability!r}")
        return self.executor.run_turn(candidates[0], conversation_id=conversation_id,
                                      user_input=user_input, tenant_id=tenant_id)

    def run_plan(self, plan: list[str], *, conversation_id: str | None = None,
                 user_input: str = "", tenant_id: str | None = None) -> list[AgentResult]:
        results, current_input = [], user_input
        for agent_name in plan:
            spec = self.registry.get(agent_name)
            res = self.executor.run_turn(spec, conversation_id=conversation_id,
                                         user_input=current_input, tenant_id=tenant_id)
            results.append(res)
            # An agent can hand off to a different next agent dynamically.
            current_input = res.text or current_input
        return results


class AgentRuntime:
    """Runs an agent plan with checkpointing, recovery, and interruption."""

    def __init__(self, coordinator: AgentCoordinator | None = None):
        self.coordinator = coordinator or AgentCoordinator()
        self._cancels: dict[str, threading.Event] = {}

    # ── Persistence ─────────────────────────────────────────────────────────────
    def _save(self, run_id: str, tenant_id: str, status: str, state: dict) -> None:
        with database.get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE agent_runs SET status=?, state=?, updated_at=DATETIME('now') "
                    "WHERE run_id=?", (status, json.dumps(state), run_id))
            else:
                conn.execute(
                    "INSERT INTO agent_runs (run_id, tenant_id, status, state) "
                    "VALUES (?, ?, ?, ?)", (run_id, tenant_id, status, json.dumps(state)))

    def get_run(self, run_id: str) -> dict | None:
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["state"] = json.loads(out["state"]) if out["state"] else {}
        return out

    def interrupt(self, run_id: str) -> None:
        if run_id in self._cancels:
            self._cancels[run_id].set()

    # ── Execution ──────────────────────────────────────────────────────────────
    def start(self, plan: list[str], *, conversation_id: str | None = None,
              user_input: str = "", tenant_id: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        tenant_id = tenant_id or tenancy.current_tenant()
        self._cancels[run_id] = threading.Event()
        state = {"plan": plan, "step": 0, "conversation_id": conversation_id,
                 "user_input": user_input, "results": []}
        self._save(run_id, tenant_id, RUNNING, state)
        self._execute(run_id, tenant_id, state)
        return run_id            # handle for get_run / resume / interrupt

    def resume(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"no agent run {run_id}")
        self._cancels.setdefault(run_id, threading.Event())
        self._execute(run_id, run["tenant_id"], run["state"])
        return run_id

    def _execute(self, run_id: str, tenant_id: str, state: dict) -> str:
        plan = state["plan"]
        current_input = (state["results"][-1]["text"] if state["results"]
                         else state["user_input"])
        cancel = self._cancels.get(run_id)
        for step in range(state["step"], len(plan)):
            if cancel and cancel.is_set():
                self._save(run_id, tenant_id, CANCELLED, state)
                return CANCELLED
            try:
                res = self.coordinator.delegate(
                    plan[step], conversation_id=state["conversation_id"],
                    task=current_input, tenant_id=tenant_id)
            except Exception as exc:  # noqa: BLE001
                state["error"] = str(exc)
                self._save(run_id, tenant_id, FAILED, state)
                return FAILED
            state["results"].append(asdict(res))
            state["step"] = step + 1
            current_input = res.text or current_input
            self._save(run_id, tenant_id, RUNNING, state)   # checkpoint per step
        self._save(run_id, tenant_id, COMPLETED, state)
        return COMPLETED
