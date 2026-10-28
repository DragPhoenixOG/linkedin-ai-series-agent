"""Decide whether today is a posting day.

GitHub Actions cron can't express "every 3 days" cleanly (`*/3` in the day field resets each
month), so the workflow runs daily and this module decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ScheduleDecision:
    """Whether to post today, and a one-line reason for the log."""

    should_post: bool
    reason: str
    days_since_last: int | None = None


def is_posting_day(today: date, last_posted: date | None, interval_days: int) -> bool:
    """True if nothing was posted yet or at least `interval_days` have passed."""
    if interval_days < 1:
        raise ValueError("interval_days must be at least 1")
    if last_posted is None:
        return True
    return (today - last_posted).days >= interval_days


def next_posting_date(last_posted: date | None, interval_days: int, today: date) -> date:
    """The first date on or after `today` that is a posting day."""
    if last_posted is None:
        return today
    return max(today, last_posted + timedelta(days=interval_days))


def decide(
    today: date, last_posted: date | None, interval_days: int, force: bool = False
) -> ScheduleDecision:
    """Combine the interval rule with the manual `force` override."""
    days = None if last_posted is None else (today - last_posted).days
    if force:
        return ScheduleDecision(True, "forced run (interval ignored)", days)
    if last_posted is None:
        return ScheduleDecision(True, "no posts yet", None)
    if is_posting_day(today, last_posted, interval_days):
        return ScheduleDecision(
            True, f"{days} days since last post (interval {interval_days})", days
        )
    upcoming = next_posting_date(last_posted, interval_days, today)
    return ScheduleDecision(
        False,
        f"not a posting day: {days} days since last post, next post on {upcoming.isoformat()}",
        days,
    )
