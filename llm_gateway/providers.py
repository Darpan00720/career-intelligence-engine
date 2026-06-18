"""LLM providers (v5) behind a single Provider port.

  AnthropicProvider — wraps the Anthropic SDK (lazy import; used in production).
  EchoProvider      — deterministic, offline provider for tests and as a safe
                      fallback when no API key / primary provider is unavailable.

Add OpenAI etc. by implementing the same interface; the gateway is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class Provider:
    name = "provider"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        raise NotImplementedError

    def stream(self, prompt: str, model: str, max_tokens: int = 1024, **kw):
        """Default streaming: yield the full completion as a single chunk."""
        yield self.complete(prompt, model, max_tokens, **kw).text


def _estimate_tokens(text: str) -> int:
    # ~4 chars per token heuristic; adequate for accounting when the SDK does
    # not return usage (e.g. the echo provider).
    return max(1, len(text) // 4)


class EchoProvider(Provider):
    """Offline provider: echoes a deterministic transformation of the prompt."""
    name = "echo"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        text = f"[echo:{model}] " + prompt.strip()[:max_tokens]
        return Completion(text, _estimate_tokens(prompt), _estimate_tokens(text), model)

    def stream(self, prompt: str, model: str, max_tokens: int = 1024, **kw):
        for word in self.complete(prompt, model, max_tokens, **kw).text.split():
            yield word + " "


class AnthropicProvider(Provider):  # pragma: no cover - requires API key/network
    name = "anthropic"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "input_tokens", _estimate_tokens(prompt))
        ct = getattr(usage, "output_tokens", _estimate_tokens(text))
        return Completion(text, pt, ct, model)

    def stream(self, prompt: str, model: str, max_tokens: int = 1024, **kw):
        import anthropic
        client = anthropic.Anthropic()
        with client.messages.stream(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text


class OpenAIProvider(Provider):  # pragma: no cover - requires API key/network
    name = "openai"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", _estimate_tokens(prompt))
        ct = getattr(usage, "completion_tokens", _estimate_tokens(text))
        return Completion(text, pt, ct, model)


class GeminiProvider(Provider):  # pragma: no cover - requires API key/network
    name = "gemini"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        import google.generativeai as genai
        gm = genai.GenerativeModel(model)
        resp = gm.generate_content(prompt)
        text = resp.text or ""
        return Completion(text, _estimate_tokens(prompt), _estimate_tokens(text), model)


class LocalProvider(Provider):
    """Offline/self-hosted provider. Deterministic; usable in tests and air-gapped
    deployments. Mirrors EchoProvider but tagged 'local' for routing/benchmarking."""
    name = "local"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        text = f"[local:{model}] {prompt.strip()[:max_tokens]}"
        return Completion(text, _estimate_tokens(prompt), _estimate_tokens(text), model)


class AzureOpenAIProvider(OpenAIProvider):  # pragma: no cover - requires Azure
    name = "azure_openai"

    def complete(self, prompt: str, model: str, max_tokens: int = 1024, **kw) -> Completion:
        import os
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", _estimate_tokens(prompt))
        ct = getattr(usage, "completion_tokens", _estimate_tokens(text))
        return Completion(text, pt, ct, model)
