"""Load prompt templates from `src/agent/prompts/` and content files from `content/`."""

from __future__ import annotations

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"


def render(name: str, **values: object) -> str:
    """Render `prompts/<name>.md`, substituting `$placeholders`. Missing values raise KeyError."""
    template = Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return template.substitute({k: str(v) for k, v in values.items()}).strip()


def fragment(name: str) -> str:
    """Return a static snippet from `prompts/fragments/<name>.md`."""
    return (PROMPTS_DIR / "fragments" / f"{name}.md").read_text(encoding="utf-8").strip()
