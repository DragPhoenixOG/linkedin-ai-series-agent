"""Content mix: post type (skill/news), tone (straight/humorous) and format rotation.

Rules (CLAUDE.md sections 2a and 4):
- About 1 in 3 posts is a news post, and never two news posts in a row.
- About 1 in 3 posts is humorous, and never two humorous posts in a row.
- A post never reuses the format of either of the previous 2 posts.

Type and tone are random but self-correcting. After a non-news post, a news post comes next with
probability r / (1 - r), which makes the long-run share r given that news never repeats. If the
recent share drifts more than 10 points from the target, the probability is nudged back.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

SKILL, NEWS = "skill", "news"
STRAIGHT, HUMOROUS = "straight", "humorous"
WINDOW = 9  # posts considered when correcting drift
DRIFT = 0.10


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    description: str


FORMATS: dict[str, Format] = {
    f.id: f
    for f in [
        Format(
            "observation",
            "Observation",
            "Something I noticed at work or in the industry, and what it taught me.",
        ),
        Format("contrarian", "Contrarian take", "A common belief, and why it's incomplete."),
        Format(
            "explainer",
            "Plain explainer",
            "Explain the concept like I would to a colleague, with one real-world example.",
        ),
        Format(
            "mistake",
            "Mistake / lesson",
            "A mistake people (or teams) commonly make, and what to do instead.",
        ),
        Format(
            "question",
            "Question-led",
            "Open with a genuine question I'm thinking about, then work through it.",
        ),
        Format(
            "story",
            "Mini story",
            "A short scenario. Unless it comes from the author facts, the opening words mark it "
            'as made up ("Picture...", "Say...", for example "$series_example_scenario") and it '
            'stays plainly hypothetical: no "we", "our" or "I" inside the story, and no oddly '
            "specific details that read like a real incident.",
        ),
        Format(
            "news_reaction",
            "News reaction",
            "What happened, in plain words, then my honest take on what it means.",
        ),
        Format(
            "dry_take",
            "Dry take",
            "A deadpan or sarcastic look at a trend or piece of news, "
            "with a real point underneath.",
        ),
    ]
}

# Preferred formats per (type, tone). The first entry of news and humorous lists is the natural
# fit and gets extra weight; skill/straight picks evenly for variety.
_PREFERRED: dict[tuple[str, str], list[str]] = {
    (SKILL, STRAIGHT): ["observation", "contrarian", "explainer", "mistake", "question", "story"],
    (SKILL, HUMOROUS): ["dry_take", "contrarian", "mistake", "story", "observation"],
    (NEWS, STRAIGHT): ["news_reaction", "contrarian", "question", "observation"],
    (NEWS, HUMOROUS): ["dry_take", "news_reaction", "contrarian"],
}
_LEAD_WEIGHT = {
    (SKILL, STRAIGHT): 1,
    (SKILL, HUMOROUS): 2,
    (NEWS, STRAIGHT): 3,
    (NEWS, HUMOROUS): 3,
}


def _alternating_choice(recent: list[str], target: str, ratio: float, rng: random.Random) -> bool:
    """True if the next item should be `target`, never twice in a row, long-run share `ratio`."""
    if ratio <= 0 or (recent and recent[0] == target):
        return False
    probability = min(1.0, ratio / (1 - ratio)) if ratio < 1 else 1.0
    window = recent[:WINDOW]
    if len(window) >= 3:
        share = window.count(target) / len(window)
        if share < ratio - DRIFT:
            probability = max(probability, 0.8)
        elif share > ratio + DRIFT:
            probability = min(probability, 0.2)
    return rng.random() < probability


def decide_post_type(recent_types: list[str], ratio: float, rng: random.Random) -> str:
    """Pick skill or news (`recent_types` most recent first). The first post is a skill post."""
    if not recent_types:
        return SKILL
    return NEWS if _alternating_choice(recent_types, NEWS, ratio, rng) else SKILL


def decide_tone(recent_tones: list[str], ratio: float, rng: random.Random) -> str:
    """Pick straight or humorous. `recent_tones` is most recent first."""
    return HUMOROUS if _alternating_choice(recent_tones, HUMOROUS, ratio, rng) else STRAIGHT


def choose_format(
    recent_formats: list[str], post_type: str, tone: str, rng: random.Random
) -> Format:
    """Pick a format that fits the type and tone and differs from the previous 2 posts."""
    blocked = set(recent_formats[:2])
    preferred = _PREFERRED[(post_type, tone)]
    options = [f for f in preferred if f not in blocked]
    if not options:
        options = [f for f in FORMATS if f not in blocked]
    weights = [1] * len(options)
    if options[0] == preferred[0]:
        weights[0] = _LEAD_WEIGHT[(post_type, tone)]
    return FORMATS[rng.choices(options, weights=weights, k=1)[0]]


def check_overrides(
    recent_types: list[str], recent_tones: list[str], post_type: str | None, tone: str | None
) -> list[str]:
    """Warnings for manual overrides that break the mix rules (they still take effect)."""
    warnings = []
    if post_type == NEWS and recent_types[:1] == [NEWS]:
        warnings.append("override makes two news posts in a row")
    if tone == HUMOROUS and recent_tones[:1] == [HUMOROUS]:
        warnings.append("override makes two humorous posts in a row")
    return warnings
