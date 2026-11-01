"""Content-mix rules: ratios, no back-to-back news or humour, format rotation."""

from __future__ import annotations

import itertools
import random

import pytest

from agent.mix import (
    FORMATS,
    HUMOROUS,
    NEWS,
    SKILL,
    STRAIGHT,
    check_overrides,
    choose_format,
    decide_post_type,
    decide_tone,
)


def simulate(n: int, seed: int) -> tuple[list[str], list[str], list[str]]:
    """Run the mix for n posts; return types, tones, formats in posting order."""
    rng = random.Random(seed)
    types: list[str] = []
    tones: list[str] = []
    formats: list[str] = []
    for _ in range(n):
        recent_types, recent_tones, recent_formats = types[::-1], tones[::-1], formats[::-1]
        post_type = decide_post_type(recent_types, 0.33, rng)
        tone = decide_tone(recent_tones, 0.33, rng)
        fmt = choose_format(recent_formats, post_type, tone, rng)
        types.append(post_type)
        tones.append(tone)
        formats.append(fmt.id)
    return types, tones, formats


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_never_two_news_or_two_humorous_in_a_row(seed: int) -> None:
    types, tones, _ = simulate(600, seed)
    assert all(not (a == b == NEWS) for a, b in itertools.pairwise(types))
    assert all(not (a == b == HUMOROUS) for a, b in itertools.pairwise(tones))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_long_run_ratios_are_about_one_in_three(seed: int) -> None:
    types, tones, _ = simulate(1500, seed)
    assert 0.29 <= types.count(NEWS) / len(types) <= 0.37
    assert 0.29 <= tones.count(HUMOROUS) / len(tones) <= 0.37


def test_short_run_ratio_stays_close() -> None:
    # Over any 9-post window the drift correction keeps news from vanishing or dominating.
    types, _, _ = simulate(300, 7)
    for start in range(0, 290, 9):
        window = types[start : start + 9]
        assert 1 <= window.count(NEWS) <= 5


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_format_never_repeats_previous_two(seed: int) -> None:
    _, _, formats = simulate(600, seed)
    for i in range(2, len(formats)):
        assert formats[i] not in (formats[i - 1], formats[i - 2])


def test_all_combinations_occur() -> None:
    types, tones, _ = simulate(300, 11)
    combos = set(zip(types, tones, strict=True))
    assert combos == {(SKILL, STRAIGHT), (SKILL, HUMOROUS), (NEWS, STRAIGHT), (NEWS, HUMOROUS)}


def test_first_post_is_a_skill_post() -> None:
    assert all(decide_post_type([], 0.33, random.Random(s)) == SKILL for s in range(50))


def test_news_never_follows_news() -> None:
    assert all(decide_post_type([NEWS, SKILL], 0.99, random.Random(s)) == SKILL for s in range(50))


def test_zero_ratio_disables_news_and_humour() -> None:
    rng = random.Random(0)
    assert all(decide_post_type([SKILL], 0.0, rng) == SKILL for _ in range(50))
    assert all(decide_tone([STRAIGHT], 0.0, rng) == STRAIGHT for _ in range(50))


def test_drift_correction_pushes_news_when_overdue() -> None:
    recent = [SKILL] * 9
    picks = [decide_post_type(recent, 0.33, random.Random(s)) for s in range(400)]
    assert picks.count(NEWS) / len(picks) >= 0.7


def test_news_formats_prefer_news_reaction() -> None:
    picks = [choose_format([], NEWS, STRAIGHT, random.Random(s)).id for s in range(400)]
    assert picks.count("news_reaction") > max(
        picks.count(f) for f in FORMATS if f != "news_reaction"
    )


def test_format_falls_back_to_any_unblocked_format() -> None:
    fmt = choose_format(["dry_take", "news_reaction"], NEWS, HUMOROUS, random.Random(0))
    assert fmt.id == "contrarian"


def test_override_warnings() -> None:
    assert check_overrides([NEWS], [HUMOROUS], NEWS, HUMOROUS) == [
        "override makes two news posts in a row",
        "override makes two humorous posts in a row",
    ]
    assert check_overrides([SKILL], [STRAIGHT], NEWS, HUMOROUS) == []


def test_mini_story_asks_for_an_explicitly_hypothetical_opening() -> None:
    from agent.mix import FORMATS
    from agent.templates import fill

    story = fill(FORMATS["story"].description)
    assert '"Picture...", "Say...", for example "Say a retailer\'s returns agent' in story
    assert '"we"' in story
