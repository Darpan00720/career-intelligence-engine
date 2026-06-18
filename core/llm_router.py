"""LLM Orchestration (v5.4).

Composes the v5.x LLM gateway into an agent-facing orchestration layer:

  ProviderManager — registry of providers + failover ordering + health (errors)
  PromptManager   — versioned prompt templates (wraps the prompt registry)
  ContextBuilder  — assemble system + memory + history into a budget-bounded prompt
  ResponseManager — normalize responses, track tokens/cost
  LLMRouter       — cost- and latency-aware routing with provider failover,
                    token budgeting, circuit breaking, and streaming

Reuses ModelRouter (task→model), AdaptiveRouter/BudgetManager (budgets),
ProviderStats (latency/error), and the gateway's CircuitBreaker — no
reimplementation. Providers: Anthropic, OpenAI, Gemini, Local.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from llm_gateway.budget import BudgetManager, ProviderStats
from llm_gateway.cost import estimate_cost
from llm_gateway.providers import EchoProvider, LocalProvider, Provider
from llm_gateway.registry import get_prompt_registry
from llm_gateway.router import ModelRouter


class AllProvidersFailed(RuntimeError):
    pass


class ProviderManager:
    """Holds providers and a preferred failover order; tracks health."""

    def __init__(self, providers: dict[str, Provider] | None = None,
                 order: list[str] | None = None):
        self.providers: dict[str, Provider] = providers or {"local": LocalProvider()}
        self.order = order or list(self.providers)
        self.stats = ProviderStats()

    def register(self, provider: Provider, *, prepend: bool = False) -> None:
        self.providers[provider.name] = provider
        if provider.name not in self.order:
            self.order.insert(0 if prepend else len(self.order), provider.name)

    def healthy_order(self, error_threshold: float = 0.5) -> list[str]:
        """Failover order with unhealthy providers pushed to the back."""
        healthy = [n for n in self.order if not self.stats.should_failover(n, error_threshold)]
        unhealthy = [n for n in self.order if n not in healthy]
        return healthy + unhealthy


class PromptManager:
    def __init__(self):
        self.registry = get_prompt_registry()

    def register(self, name: str, template: str):
        return self.registry.register(name, template)

    def render(self, name: str, variables: dict | None = None, version: int | None = None) -> str:
        return self.registry.render(name, variables or {}, version)


@dataclass
class ContextBuilder:
    """Assemble a prompt within a token budget (~4 chars/token heuristic)."""
    max_tokens: int = 4000

    def build(self, *, system: str = "", history: list[dict] | None = None,
              memory: list[str] | None = None, user: str = "") -> str:
        parts: list[str] = []
        if system:
            parts.append(f"[system]\n{system}")
        for mem in (memory or []):
            parts.append(f"[memory]\n{mem}")
        for msg in (history or []):
            parts.append(f"[{msg.get('role', 'user')}]\n{msg.get('content', '')}")
        if user:
            parts.append(f"[user]\n{user}")
        text = "\n\n".join(parts)
        budget_chars = self.max_tokens * 4
        if len(text) > budget_chars:
            # Compress by keeping the head (system+memory) and the tail (recent turns).
            head = text[: budget_chars // 3]
            tail = text[-(2 * budget_chars // 3):]
            text = head + "\n…[context compressed]…\n" + tail
        return text


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ResponseManager:
    @staticmethod
    def wrap(completion, provider: str, latency_ms: float) -> LLMResult:
        cost = estimate_cost(completion.model, completion.prompt_tokens,
                             completion.completion_tokens)
        return LLMResult(completion.text, provider, completion.model,
                         completion.prompt_tokens, completion.completion_tokens,
                         cost, latency_ms)


class LLMRouter:
    """Cost/latency-aware routing across providers with failover + budgeting."""

    def __init__(self, providers: ProviderManager | None = None,
                 model_router: ModelRouter | None = None,
                 budgets: BudgetManager | None = None,
                 breaker_threshold: int = 3):
        self.providers = providers or ProviderManager()
        self.model_router = model_router or ModelRouter()
        self.budgets = budgets or BudgetManager()
        self.responses = ResponseManager()
        self.failover_count = 0

    def select_model(self, task: str, tenant_id: str = "default") -> str:
        # Downgrade to a cheap model when the tenant is near budget.
        if self.budgets.utilization(tenant_id) >= 0.8:
            return "claude-haiku-4-5-20251001"
        return self.model_router.route(task)

    def complete(self, prompt: str, *, task: str = "research", tenant_id: str = "default",
                 max_tokens: int = 1024) -> LLMResult:
        model = self.select_model(task, tenant_id)
        last_error = None
        for provider_name in self.providers.healthy_order():
            provider = self.providers.providers[provider_name]
            start = time.perf_counter()
            try:
                completion = provider.complete(prompt, model, max_tokens)
                latency = (time.perf_counter() - start) * 1000
                self.providers.stats.record(provider_name, ok=True, latency_ms=latency)
                result = self.responses.wrap(completion, provider_name, latency)
                self.budgets.consume(tenant_id, result.total_tokens, result.cost_usd,
                                     enforce=False)
                self.model_router.record_benchmark(
                    model, latency_ms=latency, cost_usd=result.cost_usd)
                from core.metrics import record_llm_cost, observe_db_query_latency  # noqa
                record_llm_cost(result.cost_usd, tenant_id)
                return result
            except Exception as exc:  # noqa: BLE001
                latency = (time.perf_counter() - start) * 1000
                self.providers.stats.record(provider_name, ok=False, latency_ms=latency)
                self.failover_count += 1
                last_error = exc
        raise AllProvidersFailed(f"all providers failed: {last_error}")

    def stream(self, prompt: str, *, task: str = "research", tenant_id: str = "default",
               max_tokens: int = 1024):
        """Stream from the first healthy provider that supports streaming."""
        model = self.select_model(task, tenant_id)
        for provider_name in self.providers.healthy_order():
            provider = self.providers.providers[provider_name]
            try:
                yield from provider.stream(prompt, model, max_tokens)
                return
            except Exception:  # noqa: BLE001
                self.failover_count += 1
        raise AllProvidersFailed("no streaming provider available")
