"""Multi-Agent Career Workflow (v5.5).

Orchestrates the six career-intelligence agents into one explainable pipeline:

    Resume → Skills → Gap Analysis → ┬→ Recommendation ─┐
                                     └→ Learning Path  ──┴→ Explanation

Built on the v5.4 agent platform primitives (shared memory, checkpointing,
interruption) plus a dependency-aware scheduler:

  * **parallel execution** — Recommendation and Learning Path both depend only on
    Gap Analysis, so they run concurrently.
  * **checkpointing + recovery** — the shared blackboard + completed-step set is
    persisted to ``agent_runs`` after every step; ``resume()`` re-runs only the
    remaining steps.
  * **interruptible** — a per-run cancel event is checked before each step.
  * **human-in-the-loop** — when ``require_approval`` is set the run pauses at
    ``AWAITING_APPROVAL`` before recommendations; ``approve()`` + ``resume()``
    continues.
  * **agent memory sharing** — a shared ``MemoryManager`` lets later agents read
    what earlier agents learned.

All work is deterministic and offline (it reuses the v5.5 intelligence layers),
so the whole workflow is unit-testable without external services.
"""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from core import database, tenancy
from core.career_graph import CareerGraph
from core.memory_manager import MemoryManager
from core.metrics import registry as _metrics
from core.recommendation_engine import RecommendationEngine
from core.resume_intelligence import ResumeNormalizer
from core.skill_intelligence import SkillGapAnalyzer, SkillProfile, SkillProfiler
from core.tracing import span

PENDING, RUNNING, AWAITING_APPROVAL, COMPLETED, FAILED, CANCELLED = (
    "PENDING", "RUNNING", "AWAITING_APPROVAL", "COMPLETED", "FAILED", "CANCELLED")


# ── Agents ───────────────────────────────────────────────────────────────────────

class _BaseCareerAgent:
    name = "agent"
    capabilities: list[str] = []

    def __init__(self, *, memory: MemoryManager | None = None):
        self.memory = memory

    def _share(self, content: str, tenant_id: str | None = None) -> None:
        if self.memory:
            self.memory.remember(content, scope="career_workflow", tenant_id=tenant_id)


class ResumeAgent(_BaseCareerAgent):
    name = "resume"
    capabilities = ["resume_parsing"]

    def __init__(self, *, normalizer: ResumeNormalizer | None = None, **kw):
        super().__init__(**kw)
        self.normalizer = normalizer or ResumeNormalizer()

    def run(self, bb: dict, *, tenant_id: str | None = None) -> dict:
        profile = self.normalizer.normalize(bb["resume_text"], full_name=bb.get("full_name", ""))
        self.normalizer.save(profile, user_id=bb.get("user_id", ""), tenant_id=tenant_id)
        bb["profile"] = profile.to_dict()
        self._share(f"Candidate {profile.full_name or 'unknown'} parsed: "
                    f"{len(profile.skills)} skills, {profile.experience_months}mo experience",
                    tenant_id=tenant_id)
        return {"profile_id": profile.profile_id, "confidence": profile.confidence,
                "missing": profile.missing}


class SkillsAgent(_BaseCareerAgent):
    name = "skills"
    capabilities = ["skill_profiling"]

    def __init__(self, *, profiler: SkillProfiler | None = None, **kw):
        super().__init__(**kw)
        self.profiler = profiler or SkillProfiler()

    def run(self, bb: dict, *, tenant_id: str | None = None) -> dict:
        sp = self.profiler.build(bb["profile"])
        bb["skill_profile"] = {"hard": sp.hard, "soft": sp.soft,
                               "transferable": sp.transferable}
        return {"hard": len(sp.hard), "soft": len(sp.soft),
                "transferable": len(sp.transferable)}


class GapAnalysisAgent(_BaseCareerAgent):
    name = "gap_analysis"
    capabilities = ["gap_analysis"]

    def __init__(self, *, analyzer: SkillGapAnalyzer | None = None, **kw):
        super().__init__(**kw)
        self.analyzer = analyzer or SkillGapAnalyzer()

    def run(self, bb: dict, *, tenant_id: str | None = None) -> dict:
        sp = _rebuild_profile(bb["skill_profile"])
        gap = self.analyzer.analyze(sp, bb["target_role"], tenant_id=tenant_id)
        bb["gap"] = gap
        return {"coverage": gap["coverage"], "gaps": len(gap["gaps"])}


class RecommendationAgent(_BaseCareerAgent):
    name = "recommendation"
    capabilities = ["recommendation"]

    def __init__(self, *, engine: RecommendationEngine | None = None, **kw):
        super().__init__(**kw)
        self.engine = engine or RecommendationEngine()

    def run(self, bb: dict, *, tenant_id: str | None = None) -> dict:
        items = self.engine.recommend_roles(
            bb["profile"], candidate_roles=[bb["target_role"]],
            demand=bb.get("demand"), persist=True, tenant_id=tenant_id)
        bb["recommendations"] = [it.to_dict() for it in items]
        return {"count": len(items),
                "top_score": items[0].score if items else 0.0}


class LearningPathAgent(_BaseCareerAgent):
    name = "learning_path"
    capabilities = ["learning_path"]

    def __init__(self, *, engine: RecommendationEngine | None = None, **kw):
        super().__init__(**kw)
        self.engine = engine or RecommendationEngine()

    def run(self, bb: dict, *, tenant_id: str | None = None) -> dict:
        path = self.engine.recommend_learning(
            bb["profile"], bb["target_role"], persist=True, tenant_id=tenant_id)
        bb["learning_path"] = path
        return {"total_steps": path["total_steps"]}


class ExplanationAgent(_BaseCareerAgent):
    name = "explanation"
    capabilities = ["explanation"]

    def run(self, bb: dict, *, tenant_id: str | None = None) -> dict:
        gap = bb.get("gap", {})
        recs = bb.get("recommendations", [])
        path = bb.get("learning_path", {})
        top = recs[0] if recs else {}
        lines = [
            f"For target role '{bb['target_role']}': {gap.get('coverage', 0):.0%} skill coverage.",
        ]
        if top:
            lines.append(f"Top recommendation scored {top.get('score', 0):.0f}/100 "
                         f"(confidence {top.get('confidence', 0):.0%}).")
            lines.extend(top.get("reasons", []))
        if path:
            lines.append(f"A {path.get('total_steps', 0)}-step learning path closes the gaps.")
        explanation = " ".join(lines)
        bb["explanation"] = explanation
        return {"explanation": explanation}


def _rebuild_profile(d: dict) -> SkillProfile:
    return SkillProfile(hard=d.get("hard", []), soft=d.get("soft", []),
                        transferable=d.get("transferable", []))


# ── Workflow orchestrator ─────────────────────────────────────────────────────────

class CareerWorkflow:
    # step -> dependencies
    GRAPH: dict[str, list[str]] = {
        "resume": [],
        "skills": ["resume"],
        "gap_analysis": ["skills"],
        "recommendation": ["gap_analysis"],
        "learning_path": ["gap_analysis"],
        "explanation": ["recommendation", "learning_path"],
    }

    def __init__(self, *, memory: MemoryManager | None = None,
                 graph: CareerGraph | None = None,
                 engine: RecommendationEngine | None = None,
                 require_approval: bool = False):
        self.memory = memory or MemoryManager()
        graph = graph or CareerGraph()
        engine = engine or RecommendationEngine(graph=graph)
        self.require_approval = require_approval
        self.agents = {
            "resume": ResumeAgent(memory=self.memory),
            "skills": SkillsAgent(memory=self.memory),
            "gap_analysis": GapAnalysisAgent(
                analyzer=SkillGapAnalyzer(graph), memory=self.memory),
            "recommendation": RecommendationAgent(engine=engine, memory=self.memory),
            "learning_path": LearningPathAgent(engine=engine, memory=self.memory),
            "explanation": ExplanationAgent(memory=self.memory),
        }
        # steps that may run concurrently (same dependency set, no inter-dependence)
        self._parallel_groups = [["recommendation", "learning_path"]]
        self._cancels: dict[str, threading.Event] = {}

    # ── persistence (reuses agent_runs) ───────────────────────────────────────────
    def _save(self, run_id: str, tenant_id: str, status: str, state: dict) -> None:
        with database.get_connection() as conn:
            exists = conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?",
                                  (run_id,)).fetchone()
            if exists:
                conn.execute("UPDATE agent_runs SET status=?, state=?, "
                             "updated_at=DATETIME('now') WHERE run_id=?",
                             (status, json.dumps(state), run_id))
            else:
                conn.execute("INSERT INTO agent_runs (run_id, tenant_id, status, state) "
                             "VALUES (?, ?, ?, ?)",
                             (run_id, tenant_id, status, json.dumps(state)))

    def get_run(self, run_id: str) -> dict | None:
        with database.get_connection() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?",
                               (run_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["state"] = json.loads(out["state"]) if out["state"] else {}
        return out

    def interrupt(self, run_id: str) -> None:
        if run_id in self._cancels:
            self._cancels[run_id].set()

    def approve(self, run_id: str) -> None:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        state = run["state"]
        state["blackboard"]["approved"] = True
        self._save(run_id, run["tenant_id"], state.get("status", RUNNING), state)

    # ── execution ─────────────────────────────────────────────────────────────────
    def start(self, resume_text: str, target_role: str, *, full_name: str = "",
              user_id: str = "", demand: dict | None = None,
              tenant_id: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        tenant_id = tenant_id or tenancy.current_tenant()
        self._cancels[run_id] = threading.Event()
        state = {"blackboard": {"resume_text": resume_text, "target_role": target_role,
                                "full_name": full_name, "user_id": user_id,
                                "demand": demand or {}, "approved": not self.require_approval},
                 "completed": [], "results": {}}
        self._save(run_id, tenant_id, RUNNING, state)
        self._execute(run_id, tenant_id, state)
        return run_id

    def resume(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        self._cancels.setdefault(run_id, threading.Event())
        self._execute(run_id, run["tenant_id"], run["state"])
        return run_id

    def _ready_steps(self, completed: set[str]) -> list[str]:
        return [s for s, deps in self.GRAPH.items()
                if s not in completed and all(d in completed for d in deps)]

    def _execute(self, run_id: str, tenant_id: str, state: dict) -> str:
        cancel = self._cancels.get(run_id)
        bb = state["blackboard"]
        completed = set(state["completed"])
        with tenancy.use_tenant(tenant_id):
            while len(completed) < len(self.GRAPH):
                if cancel and cancel.is_set():
                    state["completed"] = sorted(completed)
                    self._save(run_id, tenant_id, CANCELLED, state)
                    return CANCELLED
                ready = self._ready_steps(completed)
                if not ready:
                    break
                # human-in-the-loop gate before recommendations
                if "recommendation" in ready and not bb.get("approved"):
                    state["completed"] = sorted(completed)
                    state["status"] = AWAITING_APPROVAL
                    self._save(run_id, tenant_id, AWAITING_APPROVAL, state)
                    return AWAITING_APPROVAL
                # find a parallel group fully contained in `ready`
                batch = self._select_batch(ready)
                try:
                    self._run_batch(batch, bb, state, tenant_id)
                except Exception as exc:  # noqa: BLE001
                    state["error"] = str(exc)
                    state["completed"] = sorted(completed)
                    self._save(run_id, tenant_id, FAILED, state)
                    return FAILED
                completed.update(batch)
                state["completed"] = sorted(completed)
                self._save(run_id, tenant_id, RUNNING, state)   # checkpoint per batch
        self._save(run_id, tenant_id, COMPLETED, state)
        return COMPLETED

    def _select_batch(self, ready: list[str]) -> list[str]:
        for group in self._parallel_groups:
            if all(g in ready for g in group):
                return group
        return [ready[0]]

    def _run_batch(self, batch: list[str], bb: dict, state: dict, tenant_id: str) -> None:
        def _one(step: str) -> tuple[str, dict]:
            with tenancy.use_tenant(tenant_id), span(f"career.{step}"):
                _metrics().inc("recommendation_requests"
                               if step == "recommendation" else f"career_{step}_requests")
                return step, self.agents[step].run(bb, tenant_id=tenant_id)

        if len(batch) == 1:
            step, result = _one(batch[0])
            state["results"][step] = result
            return
        # parallel execution for independent steps
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            for step, result in pool.map(_one, batch):
                state["results"][step] = result
