import json
from pathlib import Path
from core.config import ROLE_DICT_PATH

_cache: dict | None = None


def load() -> dict:
    """
    Load role_dictionary.json and return a dict with:
      - version: str
      - categories: the full category objects (includes primary/secondary skill weights)
      - lookup: flat dict of {title_lowercase: category_name}
    """
    global _cache
    if _cache is not None:
        return _cache

    path = Path(ROLE_DICT_PATH)
    if not path.exists():
        raise FileNotFoundError(f"role_dictionary.json not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lookup: dict[str, str] = {}
    for category, info in data["categories"].items():
        for title in info["titles"]:
            lookup[title.lower()] = category

    _cache = {
        "version":    data["version"],
        "categories": data["categories"],
        "lookup":     lookup,
    }
    return _cache


def classify_title(title: str) -> str:
    """
    Given a job title string, return the best matching role category name.
    Returns "unknown" if no match is found.

    Matching order:
    1. Exact match (case-insensitive)
    2. Longest known title that appears as a substring of the given title
    """
    dictionary = load()
    title_lower = title.lower().strip()

    if title_lower in dictionary["lookup"]:
        return dictionary["lookup"][title_lower]

    best_match   = "unknown"
    best_len     = 0
    for known_title, category in dictionary["lookup"].items():
        if known_title in title_lower and len(known_title) > best_len:
            best_match = category
            best_len   = len(known_title)

    return best_match


def get_skill_weights(role_category: str) -> dict[str, str]:
    """
    Return the skill weighting for a role category.
    Keys: category names. Values: "primary" or "secondary".
    Used by the Scoring Agent to build a role-weighted analysis for Claude.
    """
    dictionary = load()
    categories = dictionary["categories"]

    if role_category not in categories:
        return {}

    info    = categories[role_category]
    weights = {}
    for cat in info.get("primary_skill_categories", []):
        weights[cat] = "primary"
    for cat in info.get("secondary_skill_categories", []):
        weights[cat] = "secondary"
    return weights


def clear_cache() -> None:
    global _cache
    _cache = None
