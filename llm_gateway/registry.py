"""Prompt Registry & versioning (v5.1).

Central catalog of prompt templates with immutable, content-hashed versions and
rendering. Lets prompts be A/B tested (see experiments) and rolled back without
code changes. In-memory by default; persist to a prompts table for multi-node.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromptVersion:
    name: str
    version: int
    template: str
    content_hash: str

    def render(self, **variables) -> str:
        return self.template.format(**variables)


@dataclass
class PromptRegistry:
    _store: dict[str, list[PromptVersion]] = field(default_factory=dict)

    def register(self, name: str, template: str) -> PromptVersion:
        versions = self._store.setdefault(name, [])
        content_hash = hashlib.sha256(template.encode()).hexdigest()[:12]
        # Idempotent: identical content reuses the latest version.
        if versions and versions[-1].content_hash == content_hash:
            return versions[-1]
        pv = PromptVersion(name, len(versions) + 1, template, content_hash)
        versions.append(pv)
        return pv

    def get(self, prompt_name: str, version: int | None = None) -> PromptVersion:
        versions = self._store.get(prompt_name)
        if not versions:
            raise KeyError(f"unknown prompt: {prompt_name}")
        if version is None:
            return versions[-1]
        for pv in versions:
            if pv.version == version:
                return pv
        raise KeyError(f"prompt {prompt_name} has no version {version}")

    def render(self, prompt_name: str, variables: dict | None = None,
               version: int | None = None, **kw) -> str:
        # `variables` dict avoids collisions when a template var is named e.g.
        # 'name' or 'version'; **kw is the convenient form for other vars.
        return self.get(prompt_name, version).render(**{**(variables or {}), **kw})

    def versions(self, prompt_name: str) -> list[PromptVersion]:
        return list(self._store.get(prompt_name, []))

    def list_prompts(self) -> list[str]:
        return sorted(self._store)


_registry: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry


def reset_prompt_registry() -> None:
    global _registry
    _registry = None
