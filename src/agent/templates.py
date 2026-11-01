"""Load prompt templates from `src/agent/prompts/` and fill them in.

Prompts take two kinds of `$placeholders`: values passed to `render` for this call, and the
series settings from `content/series.yaml` (`$author_name`, `$series_theme`, ...), which are
loaded once per run with `use_series` and filled into every prompt and fragment.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from agent.series import Series

PROMPTS_DIR = Path(__file__).parent / "prompts"

_CONTEXT: dict[str, str] = {}


def use_series(series: Series) -> None:
    """Make the series settings available to every prompt rendered from now on."""
    _CONTEXT.clear()
    _CONTEXT.update(series.values)


def setting(name: str) -> str:
    """One series setting, e.g. `setting("author_first_name")`. Raises KeyError if unset."""
    return _CONTEXT[name]


def fill(text: str) -> str:
    """Fill series settings into a piece of text that isn't a prompt file (a format description)."""
    return Template(text).substitute(_CONTEXT)


def render(name: str, **values: object) -> str:
    """Render `prompts/<name>.md`, substituting `$placeholders`. Missing values raise KeyError."""
    template = Template((PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return template.substitute({**_CONTEXT, **{k: str(v) for k, v in values.items()}}).strip()


def fragment(name: str) -> str:
    """Return a snippet from `prompts/fragments/<name>.md`, with the series settings filled in."""
    text = (PROMPTS_DIR / "fragments" / f"{name}.md").read_text(encoding="utf-8")
    return Template(text).substitute(_CONTEXT).strip()
