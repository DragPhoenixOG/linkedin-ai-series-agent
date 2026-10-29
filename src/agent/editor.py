"""Editor pass: a separate Claude call acting as a blunt human editor.

It scores the draft on "sounds like a real person" (and, for humorous posts, on "actually funny
and still professional"), lists every sentence that sounds like AI, corporate or generic, flags
unsupported facts, and returns a rewritten version. The rewrite is kept for reference only; the
writer fixes the listed problems (see `generate.py`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent.config import Config
from agent.llm import LLM
from agent.logs import log_event
from agent.templates import fragment, render
from agent.writer import PostBrief, format_recent_posts, tidy_post

logger = logging.getLogger(__name__)

EDITOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "human_score": {"type": "integer"},
        "humour_score": {"type": "integer"},
        "ai_sounding": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"sentence": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["sentence", "reason"],
                "additionalProperties": False,
            },
        },
        "fact_problems": {"type": "array", "items": {"type": "string"}},
        "rule_problems": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
        "rewrite": {"type": "string"},
    },
    "required": [
        "human_score",
        "humour_score",
        "ai_sounding",
        "fact_problems",
        "rule_problems",
        "verdict",
        "rewrite",
    ],
    "additionalProperties": False,
}


@dataclass
class EditorReview:
    """The editor's verdict on one draft."""

    human_score: int
    humour_score: int | None
    rewrite: str
    ai_sounding: list[dict[str, str]] = field(default_factory=list)
    fact_problems: list[str] = field(default_factory=list)
    rule_problems: list[str] = field(default_factory=list)
    verdict: str = ""

    def is_complete(self, humorous: bool) -> bool:
        """A real review: scores in 1-10, not an empty template."""
        humour_ok = not humorous or 1 <= (self.humour_score or 0) <= 10
        return 1 <= self.human_score <= 10 and humour_ok

    def feedback(self) -> list[str]:
        """Editor findings phrased as items the writer must fix."""
        items = [f'Sounds like AI: "{a["sentence"]}" ({a["reason"]})' for a in self.ai_sounding]
        items += [f"Fact problem: {p}" for p in self.fact_problems]
        items += [f"Style rule problem: {p}" for p in self.rule_problems]
        return items


def _clamp_score(value: Any) -> int:
    try:
        return max(0, min(10, int(value)))
    except (TypeError, ValueError):
        return 0


# Weaker models fill the problem lists with notes that something is fine ("No factual problems
# found", "One emoji is allowed (max 1)"). Those must not count as problems. Anything that isn't
# clearly such a note stays a problem, and quoted post text is ignored when deciding.
_QUOTED = re.compile(
    r'"[^"]*"|\u201c[^\u201d]*\u201d|(?<!\w)[\'\u2018][^\'\u2019]+[\'\u2019](?!\w)'
)
_NOTHING_FOUND = re.compile(
    r"^((none|n/?a|nil|nothing( to (flag|report|fix))?)\W*$"
    r"|no ((factual|fact|rule|style|real|major|other|obvious) )?"
    r"(problems?|issues?|violations?|concerns?|errors?)\b|no unsupported claims?\b"
    r"|everything (checks out|is fine)|all (claims|facts) (are )?(supported|accurate|correct))",
    re.IGNORECASE,
)
_LOOKS_FINE = re.compile(
    r"correctly cited|\b(is|are) (acceptable|allowed|fine|accurate|supported)\b|acceptable here"
    r"|within (the )?limits?|complies with|no violation",
    re.IGNORECASE,
)
_STILL_A_PROBLEM = re.compile(
    r"\b(not|isn't|aren't|doesn't|don't|unsupported|unverified|invented|made[- ]up|fabricated"
    r"|overstate[sd]?|exaggerat\w*|wrong|incorrect|inaccurate|false|misleading|rumou?rs?"
    r"|exceeds?|too (many|long|short)|missing|banned|violat(es?|ed|ing)|only|but|except"
    r"|however|although|though|should|must|needs?|remove|cut)\b",
    re.IGNORECASE,
)


def is_real_problem(text: str) -> bool:
    """False for blank entries and notes that say nothing is wrong."""
    text = text.strip().lstrip("-*\u2022 ").strip()
    if not text:
        return False
    outside = _QUOTED.sub(" ", text).strip()
    nothing = _NOTHING_FOUND.match(outside)
    rest = outside[nothing.end() :] if nothing else outside
    if _STILL_A_PROBLEM.search(rest):
        return True
    return not (nothing or _LOOKS_FINE.search(rest))


def _problems(items: Any) -> list[str]:
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    return [str(p).strip() for p in items if is_real_problem(str(p))]


def parse_review(data: Any, humorous: bool) -> EditorReview:
    """Turn the editor's JSON into an `EditorReview`, tolerating missing fields."""
    data = data if isinstance(data, dict) else {}
    ai_sounding = [
        {"sentence": str(a.get("sentence", "")), "reason": str(a.get("reason", ""))}
        for a in data.get("ai_sounding", [])
        if isinstance(a, dict) and a.get("sentence")
    ]
    return EditorReview(
        human_score=_clamp_score(data.get("human_score")),
        humour_score=_clamp_score(data.get("humour_score")) if humorous else None,
        rewrite=tidy_post(str(data.get("rewrite", ""))),
        ai_sounding=ai_sounding,
        fact_problems=_problems(data.get("fact_problems")),
        rule_problems=_problems(data.get("rule_problems")),
        verdict=str(data.get("verdict", "")).strip(),
    )


def edit_draft(llm: LLM, config: Config, brief: PostBrief, draft: str) -> EditorReview:
    """Score and rewrite a draft."""
    humorous = brief.tone == "humorous"
    prompt = render(
        "editor",
        post_type=brief.post_type,
        tone=brief.tone,
        format_name=brief.format_name,
        format_description=brief.format_description,
        subject_line=brief.subject_line,
        research=brief.research_notes,
        recent_posts=format_recent_posts(brief.recent_posts),
        draft=draft,
        humour_instruction=fragment("editor_humour" if humorous else "editor_straight"),
        min_words=config.min_words,
        max_words=config.max_words,
        max_hashtags=config.max_hashtags,
        constraints="\n".join(f"- {c}" for c in brief.constraints()),
    )
    request = {
        "model": config.editor_model,
        "system": [brief.voice.system_block(), render("system_editor")],
        "effort": config.editor_effort,
        "max_tokens": 16000,
        "json_schema": EDITOR_SCHEMA,
    }
    data, _ = llm.complete_json(label="editor", prompt=prompt, **request)
    review = parse_review(data, humorous)
    if review.is_complete(humorous):
        return review
    # Weaker models sometimes return the JSON shape with zeros and blanks. Ask once more.
    log_event(logger, "editor.incomplete", logging.WARNING, score=review.human_score)
    retry_prompt = f"{prompt}\n\n{fragment('editor_incomplete')}"
    data, _ = llm.complete_json(label="editor.retry", prompt=retry_prompt, **request)
    return parse_review(data, humorous)
