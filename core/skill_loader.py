import json
import re
from pathlib import Path
from core.config import SKILL_DICT_PATH

_cache: dict | None = None


def load() -> dict:
    """
    Load skill_dictionary.json and return a dict with:
      - version: str
      - categories: the full category objects
      - lookup: flat dict of {keyword_lowercase: category_name}
    """
    global _cache
    if _cache is not None:
        return _cache

    path = Path(SKILL_DICT_PATH)
    if not path.exists():
        raise FileNotFoundError(f"skill_dictionary.json not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lookup: dict[str, str] = {}
    for category, info in data["categories"].items():
        for keyword in info["keywords"]:
            lookup[keyword.lower()] = category

    _cache = {
        "version":    data["version"],
        "categories": data["categories"],
        "lookup":     lookup,
    }
    return _cache


def _matches(keyword: str, text: str) -> bool:
    """
    True if `keyword` appears in `text` (both already lowercased).
    Keywords of 3 characters or fewer use whole-word matching to prevent false positives.
    This covers single-letter skills ("r"), two-letter acronyms ("ta", "mi", "bi"),
    and three-letter acronyms ("ona", "ats", "rag", "sql", "erp", "sap", "rpa", etc.)
    that would otherwise match inside common words like "stakeholder", "milan", "ability",
    "organizational", "formats", "fragile", "smaller".
    """
    if len(keyword) <= 3:
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))
    return keyword in text


def match_keywords(text: str) -> dict[str, list[str]]:
    """
    Given job description text, return {category: [matched_keywords]}.
    Single-letter skills (e.g. "R") require whole-word matches only.
    Multi-word skills use fast substring matching.
    """
    dictionary = load()
    text_lower = text.lower()
    matches: dict[str, list[str]] = {}

    for keyword, category in dictionary["lookup"].items():
        if _matches(keyword, text_lower):
            matches.setdefault(category, []).append(keyword)

    return matches


def clear_cache() -> None:
    global _cache
    _cache = None
