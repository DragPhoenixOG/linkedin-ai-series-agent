"""Who the series is by and what it's about, from `content/series.yaml`.

Every prompt is filled in from these settings (see `templates.use_series`), so the same agent can
run a series for another person or on another subject by editing content files only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Section -> fields. Prompts use them as `$<section>_<field>`, e.g. `$author_first_name`.
FIELDS: dict[str, tuple[str, ...]] = {
    "author": ("name", "first_name", "summary", "kind", "voice", "peers", "own_work"),
    "series": (
        "theme",
        "writes_about",
        "skill_promise",
        "topic_areas",
        "learner",
        "example_scenario",
    ),
    "research": ("focus", "why_it_matters_for"),
    "news": ("focus", "major", "take"),
}


class SeriesError(ValueError):
    """`content/series.yaml` is missing, incomplete or has unknown fields."""


@dataclass(frozen=True)
class Series:
    """The series settings, flattened to the placeholder names the prompts use."""

    values: dict[str, str]

    def __getitem__(self, key: str) -> str:
        return self.values[key]


def load_series(content_dir: Path) -> Series:
    """Read and check `content/series.yaml`. Every field is required and must be non-empty."""
    path = content_dir / "series.yaml"
    if not path.exists():
        raise SeriesError(f"{path} is missing. It says who the series is by and what it's about.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SeriesError(f"{path} must hold sections: {', '.join(FIELDS)}.")
    problems: list[str] = []
    values: dict[str, str] = {}
    for section in sorted(set(data) - set(FIELDS)):
        problems.append(f"unknown section '{section}'")
    for section, names in FIELDS.items():
        given = data.get(section) or {}
        if not isinstance(given, dict):
            problems.append(f"'{section}' must be a section with fields")
            continue
        for name in sorted(set(given) - set(names)):
            problems.append(f"unknown field '{section}.{name}'")
        for name in names:
            value = given.get(name)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"'{section}.{name}' is missing or empty")
                continue
            values[f"{section}_{name}"] = " ".join(value.split())
    if problems:
        raise SeriesError(f"{path}: " + "; ".join(problems) + ".")
    return Series(values)
