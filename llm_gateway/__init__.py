"""LLM Gateway (v5) — unified, observable access to LLM providers.

Public API: LLMGateway, get_gateway(), providers, and cost utilities.
"""
from llm_gateway.cost import PRICING, CostTracker, estimate_cost
from llm_gateway.gateway import CircuitBreaker, LLMGateway, LLMResponse, get_gateway, reset_gateway
from llm_gateway.providers import (
    AnthropicProvider,
    AzureOpenAIProvider,
    EchoProvider,
    OpenAIProvider,
    Provider,
)
from llm_gateway.registry import PromptRegistry, get_prompt_registry, reset_prompt_registry
from llm_gateway.router import ModelRouter, get_router, reset_router
from llm_gateway.semantic_cache import SemanticCache

__all__ = [
    "LLMGateway", "get_gateway", "reset_gateway", "LLMResponse", "CircuitBreaker",
    "Provider", "AnthropicProvider", "EchoProvider", "OpenAIProvider", "AzureOpenAIProvider",
    "CostTracker", "estimate_cost", "PRICING",
    "PromptRegistry", "get_prompt_registry", "reset_prompt_registry",
    "ModelRouter", "get_router", "reset_router", "SemanticCache",
]
