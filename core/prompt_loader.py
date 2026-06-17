import hashlib
from pathlib import Path
from core.config import PROMPTS_DIR

_cache: dict[str, str] = {}


def load(prompt_name: str) -> str:
    """
    Load a prompt file by name (without the .txt extension).
    Example: load("scoring_prompt") reads prompts/scoring_prompt.txt
    Raises if the file is missing or still a stub.
    """
    if prompt_name in _cache:
        return _cache[prompt_name]

    path = Path(PROMPTS_DIR) / f"{prompt_name}.txt"

    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Create prompts/{prompt_name}.txt before running this agent."
        )

    text = path.read_text(encoding="utf-8").strip()

    if text.startswith("# STUB"):
        raise ValueError(
            f"Prompt '{prompt_name}' is still a stub.\n"
            f"Write the full prompt in {path} before running this agent."
        )

    _cache[prompt_name] = text
    return text


def version(prompt_name: str) -> str:
    """Return a stable 8-char content hash — changes only when file content changes."""
    path = Path(PROMPTS_DIR) / f"{prompt_name}.txt"
    if not path.exists():
        return "missing"
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def clear_cache() -> None:
    _cache.clear()
