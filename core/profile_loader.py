import json
from pathlib import Path
from core.config import PROFILE_PATH

_cache: dict | None = None


def load() -> dict:
    """Return the candidate profile. Validates that no FILL_IN fields remain."""
    global _cache
    if _cache is not None:
        return _cache

    path = Path(PROFILE_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"candidate_profile.json not found at {path}\n"
            "This file is required. If you deleted it by mistake, re-create it\n"
            "using the template structure in docs/IMPLEMENTATION_PLAN.md."
        )

    with open(path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    _validate(profile)
    _cache = profile
    return profile


def clear_cache() -> None:
    """Force a fresh read on the next load() call."""
    global _cache
    _cache = None


def _validate(profile: dict) -> None:
    required, optional = _find_fill_in(profile, "")
    messages = []

    if required:
        messages.append(
            f"{len(required)} required field(s) still contain placeholder text — fill these in:"
        )
        messages.extend(f"  → {f}" for f in required)

    if optional:
        messages.append(
            f"\n{len(optional)} optional field(s) need a decision"
            " — fill them in OR delete them from the JSON file:"
        )
        messages.extend(f"  → {f}" for f in optional)

    if messages:
        raise ValueError(
            "candidate_profile.json is not complete.\n"
            + "\n".join(messages)
        )


def _find_fill_in(obj, path: str) -> tuple[list[str], list[str]]:
    """
    Walk the JSON and return (required, optional).
      required — fields starting with "FILL_IN" that must be filled in
      optional — fields starting with "FILL_IN_OR_REMOVE" that should be filled in or deleted
    """
    required: list[str] = []
    optional: list[str] = []

    if isinstance(obj, dict):
        for key, val in obj.items():
            child = f"{path}.{key}" if path else key
            r, o = _find_fill_in(val, child)
            required.extend(r)
            optional.extend(o)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            r, o = _find_fill_in(item, f"{path}[{i}]")
            required.extend(r)
            optional.extend(o)
    elif isinstance(obj, str):
        if obj.startswith("FILL_IN_OR_REMOVE"):
            optional.append(path)
        elif obj.startswith("FILL_IN"):
            required.append(path)

    return required, optional
