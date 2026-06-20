"""LLMGateway (v5) — one front door to all LLM calls.

Responsibilities: provider selection + fallback, response caching (request
dedup), retries with backoff, a circuit breaker, cost tracking, and basic
metrics. Streaming is supported via stream().

    gw = LLMGateway(primary=AnthropicProvider(), fallback=EchoProvider())
    resp = gw.complete("Summarize...", model="claude-sonnet-4-6",
                       tenant_id="acme", workflow_id="wf1", agent="research")
    print(resp.text, resp.cost_usd, resp.cached)
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass

from core import config
from core.cache import get_cache
from core.concurrency import backoff_retry
from core.event_log import log_event
from llm_gateway.cost import CostTracker
from llm_gateway.providers import EchoProvider, Provider


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    cached: bool
    request_id: str


class CircuitBreaker:
    """Trips open after `threshold` consecutive failures; half-opens after `cooldown`."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, threshold: int = 3, cooldown: float = 30.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.state == self.OPEN:
            if time.time() - self.opened_at >= self.cooldown:
                self.state = self.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = self.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.state = self.OPEN
            self.opened_at = time.time()


class CircuitOpenError(RuntimeError):
    pass


class LLMGateway:
    def __init__(self, primary: Provider | None = None, fallback: Provider | None = None,
                 retries: int = 2, cache_ttl: float = 3600.0,
                 breaker: CircuitBreaker | None = None):
        self.primary = primary or EchoProvider()
        self.fallback = fallback
        self.retries = retries
        self.cache_ttl = cache_ttl
        self.breaker = breaker or CircuitBreaker()
        self.cost_tracker = CostTracker()
        self.cache = get_cache()

    # ── Helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _cache_key(prompt: str, model: str, tenant_id: str) -> str:
        # Tenant is part of the key so one tenant's cached completion (which may
        # embed that tenant's data in the prompt) is never served to another.
        digest = hashlib.sha256(f"{tenant_id}::{model}::{prompt}".encode()).hexdigest()[:32]
        return f"llm:{tenant_id}:{digest}"

    def _record(self, comp, provider_name, model, latency_ms, cached,
                tenant_id, workflow_id, agent, request_id) -> LLMResponse:
        cost = self.cost_tracker.record(
            request_id=request_id, tenant_id=tenant_id, workflow_id=workflow_id,
            agent=agent, provider=provider_name, model=model,
            prompt_tokens=comp.prompt_tokens, completion_tokens=comp.completion_tokens,
            latency_ms=latency_ms, cached=cached,
        )
        return LLMResponse(
            text=comp.text, model=model, provider=provider_name,
            prompt_tokens=comp.prompt_tokens, completion_tokens=comp.completion_tokens,
            cost_usd=cost, latency_ms=latency_ms, cached=cached, request_id=request_id,
        )

    # ── Completion ───────────────────────────────────────────────────────────
    def complete(self, prompt: str, model: str | None = None, *,
                 tenant_id: str | None = None, workflow_id: str | None = None,
                 agent: str | None = None, max_tokens: int = 1024,
                 use_cache: bool = True) -> LLMResponse:
        from core.tenancy import current_tenant
        model = model or config.CLAUDE_MODEL
        tenant_id = tenant_id or current_tenant()
        request_id = uuid.uuid4().hex
        key = self._cache_key(prompt, model, tenant_id)

        # Cache / request dedup.
        if use_cache:
            cached_comp = self.cache.get(key)
            if cached_comp is not None:
                from llm_gateway.providers import Completion
                comp = Completion(**cached_comp)
                return self._record(comp, "cache", model, 0.0, True,
                                    tenant_id, workflow_id, agent, request_id)

        if not self.breaker.allow():
            if self.fallback is not None:
                comp = self.fallback.complete(prompt, model, max_tokens)
                return self._record(comp, self.fallback.name, model, 0.0, False,
                                    tenant_id, workflow_id, agent, request_id)
            raise CircuitOpenError("LLM circuit open and no fallback configured")

        start = time.perf_counter()
        try:
            comp = backoff_retry(
                lambda: self.primary.complete(prompt, model, max_tokens),
                retries=self.retries, base_delay=0.1,
            )
            self.breaker.record_success()
        except Exception as exc:  # noqa: BLE001
            self.breaker.record_failure()
            log_event("pipeline", "llm_primary_failed", status="error",
                      error=str(exc), level="ERROR")
            if self.fallback is None:
                raise
            comp = self.fallback.complete(prompt, model, max_tokens)
            latency = (time.perf_counter() - start) * 1000
            return self._record(comp, self.fallback.name, model, latency, False,
                                tenant_id, workflow_id, agent, request_id)

        latency = (time.perf_counter() - start) * 1000
        if use_cache:
            self.cache.set(key, {"text": comp.text, "prompt_tokens": comp.prompt_tokens,
                                 "completion_tokens": comp.completion_tokens,
                                 "model": comp.model}, ttl=self.cache_ttl)
        return self._record(comp, self.primary.name, model, latency, False,
                            tenant_id, workflow_id, agent, request_id)

    def stream(self, prompt: str, model: str | None = None, max_tokens: int = 1024):
        """Stream tokens from the primary provider (no caching on streams)."""
        model = model or config.CLAUDE_MODEL
        yield from self.primary.stream(prompt, model, max_tokens)


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    """Default gateway: Anthropic primary with an Echo fallback when no API key."""
    global _gateway
    if _gateway is None:
        import os
        from llm_gateway.providers import AnthropicProvider
        primary = AnthropicProvider() if os.getenv("ANTHROPIC_API_KEY") else EchoProvider()
        _gateway = LLMGateway(primary=primary, fallback=EchoProvider())
    return _gateway


def reset_gateway() -> None:
    global _gateway
    _gateway = None
