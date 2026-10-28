"""Posting-day logic, including month and year boundaries."""

from __future__ import annotations

from datetime import date

import pytest

from agent.scheduler import decide, is_posting_day, next_posting_date


def test_first_ever_run_posts() -> None:
    assert is_posting_day(date(2026, 10, 28), None, 3)
    assert decide(date(2026, 10, 28), None, 3).reason == "no posts yet"


@pytest.mark.parametrize(
    ("last", "today", "expected"),
    [
        (date(2026, 10, 25), date(2026, 10, 26), False),
        (date(2026, 10, 25), date(2026, 10, 27), False),
        (date(2026, 10, 25), date(2026, 10, 28), True),
        (date(2026, 10, 25), date(2026, 10, 30), True),  # a missed run still posts later
        (date(2026, 10, 25), date(2026, 10, 25), False),  # same day: never twice
    ],
)
def test_interval_rule(last: date, today: date, expected: bool) -> None:
    assert is_posting_day(today, last, 3) is expected


@pytest.mark.parametrize(
    ("last", "today", "expected"),
    [
        (date(2026, 1, 30), date(2026, 2, 1), False),  # Jan has 31 days: only 2 days
        (date(2026, 1, 30), date(2026, 2, 2), True),
        (date(2026, 2, 27), date(2026, 3, 2), True),  # 2026 is not a leap year
        (date(2028, 2, 27), date(2028, 3, 1), True),  # 2028 is: 27 -> 29 -> 1 Mar = 3 days
        (date(2028, 2, 28), date(2028, 3, 1), False),
        (date(2026, 4, 29), date(2026, 5, 1), False),  # 30-day month
        (date(2026, 12, 30), date(2027, 1, 2), True),  # year boundary
        (date(2026, 12, 31), date(2027, 1, 2), False),
    ],
)
def test_month_and_year_boundaries(last: date, today: date, expected: bool) -> None:
    assert is_posting_day(today, last, 3) is expected


def test_cron_style_day_of_month_would_have_been_wrong() -> None:
    # `*/3` would fire on the 31st and again on the 1st; the interval rule does not.
    assert not is_posting_day(date(2026, 11, 1), date(2026, 10, 31), 3)


def test_force_ignores_interval() -> None:
    decision = decide(date(2026, 10, 26), date(2026, 10, 25), 3, force=True)
    assert decision.should_post
    assert "forced" in decision.reason


def test_skip_reason_names_next_date() -> None:
    decision = decide(date(2026, 10, 26), date(2026, 10, 25), 3)
    assert not decision.should_post
    assert "2026-10-28" in decision.reason
    assert decision.days_since_last == 1


def test_next_posting_date() -> None:
    assert next_posting_date(None, 3, date(2026, 10, 1)) == date(2026, 10, 1)
    assert next_posting_date(date(2026, 10, 1), 3, date(2026, 10, 2)) == date(2026, 10, 4)
    assert next_posting_date(date(2026, 9, 1), 3, date(2026, 10, 2)) == date(2026, 10, 2)


def test_custom_interval() -> None:
    assert not is_posting_day(date(2026, 10, 30), date(2026, 10, 25), 7)
    assert is_posting_day(date(2026, 11, 1), date(2026, 10, 25), 7)


def test_invalid_interval_rejected() -> None:
    with pytest.raises(ValueError):
        is_posting_day(date(2026, 10, 30), date(2026, 10, 25), 0)
