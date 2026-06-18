# Career Intelligence Engine v5.4 — AI Orchestration & Agent Platform (P3)

Status: **implemented and tested (999 tests, 2 Testcontainers skip without
Docker).** All components are pure-Python composition over the v5.x platform
(LLM gateway, tenancy, metrics/tracing, persistence) with DB-backed state for
conversations, memory, and agent runs.

---

## 1. Architecture (C4 — container)

```
                         ┌──────────── AgentRuntime ────────────┐
   user / API ──turn──▶  │ checkpoint(agent_runs) · interrupt    │
                         │ resume-after-failure                  │
                         └───────┬───────────────────────────────┘
                                 ▼
                         ┌──────────── AgentCoordinator ─────────┐
                         │ delegate · handoff(by capability)     │
                         │ run_plan (sequential, shared convo)   │
                         └───────┬───────────────────────────────┘
                                 ▼
        ┌──────────────── AgentExecutor (one turn) ────────────────────┐
        │ ContextAssembler ── LLMRouter ── ToolInvocationEngine          │
        └───┬───────────────────┬──────────────────────┬────────────────┘
            ▼                   ▼                      ▼
   MemoryManager         LLM Orchestration        Tool Platform
   ConversationStore     ProviderManager(failover) ToolRegistry/Sandbox
   Short/LongTermMemory  ModelRouter(cost/latency)  ToolPermissions(RBAC)
   EmbeddingService      BudgetManager(token budget)ToolExecutor(async,
   ContextAssembler      CircuitBreaker             timeout, retries, audit)
        │                   │                          │
        └──── tenancy (row-level isolation) · metrics · tracing (spans) ┘
   Persistence: conversations / conversation_messages / agent_memories / agent_runs
```

### Turn sequence
```
AgentExecutor.run_turn(spec, conversation_id, user_input)
  ctx = MemoryManager.context_for(conversation, query)
        = { summary, semantic long-term memories, recent short-term window }
  prompt = ContextBuilder.build(system, history, memory, user)   # budget-bounded
  result = LLMRouter.complete(prompt, task=spec.model_task, tenant)
           → select_model (downgrade if near budget)
           → for provider in healthy_order: try; on error record + failover
           → record cost/latency; consume budget
  (optional) ToolInvocationEngine.run_calls(tool_calls)          # permissioned, sandboxed
  MemoryManager.add_message(assistant, result.text)
```

---

## 2. Components & deliverables

| Deliverable | Classes | Notes |
|-------------|---------|-------|
| [core/llm_router.py](../core/llm_router.py) | `LLMRouter`, `ProviderManager`, `PromptManager`, `ContextBuilder`, `ResponseManager` | provider failover, cost/latency-aware routing, token budgeting, streaming, circuit breaking |
| [core/memory_manager.py](../core/memory_manager.py) | `MemoryManager`, `ConversationStore`, `ShortTermMemory`, `LongTermMemory`, `ContextAssembler`, `EmbeddingService` | tenant isolation, semantic retrieval, expiry, summarization/compression |
| [core/tool_executor.py](../core/tool_executor.py) | `ToolExecutor`, `ToolSandbox`, `ToolPermissions`, `ToolRegistry`, `ToolInvocationEngine` | async, timeouts, retries, RBAC permissions, audit, policy sandbox |
| [core/conversation_manager.py](../core/conversation_manager.py) | `ConversationManager` | long-running lifecycle + auto-compression |
| [core/agent_registry.py](../core/agent_registry.py) | `AgentRegistry`, `AgentSpec` | capability-based discovery for handoffs |
| [core/agent_runtime.py](../core/agent_runtime.py) | `AgentRuntime`, `AgentCoordinator`, `AgentExecutor`, `AgentResult` | multi-agent, delegation/handoff, checkpoint/recovery, interruptible |

Providers: **Anthropic, OpenAI, Azure OpenAI, Gemini, Local** (offline/test) all
behind the gateway `Provider` port — add one by implementing `complete`/`stream`.

---

## 3. Resilience model

- **Provider failover:** `ProviderManager.healthy_order()` reorders by error rate
  (`ProviderStats`); `LLMRouter.complete` tries each until one succeeds, else
  `AllProvidersFailed`. `failover_count` is tracked + metered.
- **Cost-aware routing:** near a tenant's budget (`BudgetManager`, ≥80% util) the
  router downgrades to a cheaper model; budgets consumed per call.
- **Checkpoint/recovery:** `AgentRuntime` persists `{plan, step, results}` to
  `agent_runs` after every step; a crash/failure leaves status `FAILED` at the
  failed step, and `resume(run_id)` continues from there (completed steps skipped).
- **Interruptible:** `interrupt(run_id)` sets a cancel event; the runtime stops
  before the next step and persists `CANCELLED`.
- **Tools:** unregistered/unauthorized calls are rejected; each call is timed,
  retried, and audited; failures are isolated per call in batch execution.

---

## 4. Observability

Metrics (`core.metrics`, scraped at `/api/v2/metrics/prometheus`):
`agent_active`, `agent_execution_time` (histogram), `tool_invocations`,
`tool_failures`, plus `llm_cost`/`provider_failover` via the router and
`db_*` from persistence. Spans (`core.tracing.span`) wrap each tool call
(`tool:<name>`) and propagate trace/correlation IDs; agent and LLM turns inherit
the active trace. Token usage + cost per call/tenant flow to the existing
`llm_costs` accounting and cost-intelligence reports.

---

## 5. Testing ([tests/test_agent_platform.py](../tests/test_agent_platform.py), 26 tests)

- **LLM router:** completion, **provider failover**, all-fail error, budget
  downgrade, streaming, context compression.
- **Memory:** embedding cosine, conversation roundtrip, **semantic retrieval**,
  **expiry**, **tenant isolation**, conversation compression.
- **Tools:** permissioned invoke, **permission denied**, unregistered tool,
  **timeout**, retry-then-succeed, batch run.
- **Agent runtime:** single turn, **delegation + handoff**, full plan,
  **checkpoint recovery after failure**, **interruption**.
- **Load/chaos:** 500-memory retrieval; flaky-provider recovery.
- **Backward compatibility:** v5.x imports intact.

The required deliverable test files map to suites within `test_agent_platform.py`
(test_agent_runtime / test_llm_router / test_memory_manager / test_tool_executor
are the `TestAgentRuntime` / `TestLLMRouter` / `TestMemory` / `TestToolExecutor`
classes).

---

## 6. Scope & next steps (honest)

Implemented with deterministic local providers + DB-backed state, so it is fully
tested offline. Production wiring that needs external services — real
Anthropic/OpenAI/Gemini keys, a vector DB for embeddings at 100k-user scale, and
OS-level tool sandboxing (seccomp/gVisor/containers) — slots behind the existing
ports (`Provider`, `EmbeddingService`, `ToolSandbox`) without changing call
sites. All v5.x APIs, migrations, Docker compatibility, and 999 tests preserved.
```
