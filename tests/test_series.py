"""content/series.yaml: loading, checking, and filling every prompt from it."""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

import pytest
import yaml

from agent import templates
from agent.mix import FORMATS
from agent.series import FIELDS, SeriesError, load_series

REPO = Path(__file__).resolve().parents[1]

AGRICULTURE = {
    "author": {
        "name": "Priya Sharma",
        "first_name": "Priya",
        "summary": "an agronomist who advises smallholder farms on soil health and irrigation",
        "kind": "agronomist",
        "voice": "a practical agronomist talking to farmers at the co-op",
        "peers": "agronomists and farm advisers",
        "own_work": "soil testing and drip irrigation work",
    },
    "series": {
        "theme": "practical farm tech",
        "writes_about": "farming, soil and water",
        "skill_promise": "why the practice matters this season and how it protects yields",
        "topic_areas": "soil health, irrigation, crop planning, farm equipment",
        "learner": "a farmer or farm adviser",
        "example_scenario": "Say a farm switches to drip irrigation in March...",
    },
    "research": {
        "focus": "a new crop variety, a government scheme, a field trial with results",
        "why_it_matters_for": "farmers and farm advisers",
    },
    "news": {
        "focus": "crop prices, monsoon forecasts, irrigation schemes, agri policy",
        "major": "a major policy change or a failed monsoon",
        "take": "what it means for small farms and whether the claims hold up",
    },
}

# Text that belongs to the current series only. None of it may reach another series' prompts.
THIS_SERIES = [
    "Ashish",
    "Ranjan",
    "OMS",
    "marketplace",
    "retail",
    "Forward Deployed",
    "future of tech",
    "enterprise reliability",
    "agentic",
    "consultant",
    "chai",
]


def write_series(directory: Path, data: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "series.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return directory


def placeholders(text: str) -> set[str]:
    template = Template(text)
    found = {m.group("named") or m.group("braced") for m in template.pattern.finditer(text)}
    return found - {None}


def render_everything() -> dict[str, str]:
    """Every prompt, fragment and format description, with call-time values as <name>."""
    out = {}
    for path in sorted(templates.PROMPTS_DIR.glob("*.md")):
        names = placeholders(path.read_text(encoding="utf-8"))
        values = {n: f"<{n}>" for n in names if n not in templates._CONTEXT}
        out[f"prompt {path.stem}"] = templates.render(path.stem, **values)
    for path in sorted((templates.PROMPTS_DIR / "fragments").glob("*.md")):
        out[f"fragment {path.stem}"] = templates.fragment(path.stem)
    for key, fmt in FORMATS.items():
        out[f"format {key}"] = templates.fill(fmt.description)
    return out


def test_repo_series_has_every_field() -> None:
    series = load_series(REPO / "content")
    expected = {f"{section}_{name}" for section, names in FIELDS.items() for name in names}
    assert set(series.values) == expected
    assert series["author_first_name"] == "Ashish"
    assert series["series_theme"] == "AI and the future of tech"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["author"].pop("first_name"), "'author.first_name' is missing or empty"),
        (lambda d: d["news"].update(take="  "), "'news.take' is missing or empty"),
        (lambda d: d["series"].update(theme=7), "'series.theme' is missing or empty"),
        (lambda d: d["author"].update(nickname="P"), "unknown field 'author.nickname'"),
        (lambda d: d.update(extra={"a": "b"}), "unknown section 'extra'"),
        (lambda d: d.update(news="crop prices"), "'news' must be a section with fields"),
    ],
)
def test_series_problems_are_reported_clearly(tmp_path: Path, change, message: str) -> None:
    data = {section: dict(values) for section, values in AGRICULTURE.items()}
    change(data)
    with pytest.raises(SeriesError, match=re.escape(message)):
        load_series(write_series(tmp_path, data))


def test_missing_series_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SeriesError, match=r"series\.yaml is missing"):
        load_series(tmp_path)


def test_multi_line_values_are_joined_into_one_sentence(tmp_path: Path) -> None:
    data = {section: dict(values) for section, values in AGRICULTURE.items()}
    data["news"]["take"] = "what it means\nfor small   farms"
    assert load_series(write_series(tmp_path, data))["news_take"] == "what it means for small farms"


def test_every_prompt_fills_completely_from_the_repo_series() -> None:
    for name, text in render_everything().items():
        assert not re.search(r"\$[A-Za-z_]{2,}", text), f"{name} has an unfilled placeholder"
    system_writer = templates.render("system_writer")
    assert system_writer.startswith("You ghostwrite LinkedIn posts for Ashish Ranjan")
    assert "the author's own OMS, marketplace, ERP, order and returns work" in system_writer


def test_another_series_carries_nothing_of_this_one(tmp_path: Path) -> None:
    templates.use_series(load_series(write_series(tmp_path, AGRICULTURE)))
    rendered = render_everything()
    for name, text in rendered.items():
        for phrase in THIS_SERIES:
            assert phrase.lower() not in text.lower(), f"{name} still says {phrase!r}"
    assert rendered["prompt system_writer"].startswith(
        "You ghostwrite LinkedIn posts for Priya Sharma, in Priya's voice"
    )
    assert "practical farm tech" in rendered["prompt subject_skill"]
    assert "crop prices, monsoon forecasts" in rendered["prompt news_scan"]
    assert "agronomists and farm advisers like Priya" in rendered["fragment tone_humorous"]
    assert "Say a farm switches to drip irrigation" in rendered["format story"]


def test_setting_reads_one_value() -> None:
    assert templates.setting("author_name") == "Ashish Ranjan"
    with pytest.raises(KeyError):
        templates.setting("author_nickname")
