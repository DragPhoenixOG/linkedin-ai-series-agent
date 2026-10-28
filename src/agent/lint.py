"""Deterministic anti-AI checks for a post (CLAUDE.md section 4).

Every rule here is a hard gate: if any check fails, the post is not published and the failures
are fed back to the writer. Checks are plain regex and counting so they are fast, free and
predictable. Where the brief names a word, inflected forms are matched too ("delve" also catches
"delving"), with the exceptions noted next to each pattern.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LintLimits:
    """Numeric limits for a post."""

    min_words: int = 120
    max_words: int = 250
    max_hashtags: int = 3
    max_emojis: int = 1
    max_em_dashes: int = 1
    max_bullets: int = 4
    max_links: int = 1

    @classmethod
    def from_config(cls, config: Any) -> LintLimits:
        """Build limits from a `Config` (or anything with the same attribute names)."""
        return cls(
            min_words=config.min_words,
            max_words=config.max_words,
            max_hashtags=config.max_hashtags,
            max_emojis=config.max_emojis,
            max_em_dashes=config.max_em_dashes,
            max_bullets=config.max_bullets,
            max_links=config.max_links,
        )


@dataclass(frozen=True)
class LintIssue:
    """One failed check. `rule` is a stable id, `detail` says what to fix."""

    rule: str
    detail: str


@dataclass
class LintResult:
    """Outcome of linting one post."""

    issues: list[LintIssue] = field(default_factory=list)
    word_count: int = 0

    @property
    def passed(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        """Human-readable list of failures, one per line."""
        if self.passed:
            return f"lint passed ({self.word_count} words)"
        return "\n".join(f"- [{i.rule}] {i.detail}" for i in self.issues)


# --- Text helpers -----------------------------------------------------------------------

_QUOTE_MAP = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def normalize(text: str) -> str:
    """Normalise curly quotes so patterns only need straight apostrophes."""
    return text.translate(_QUOTE_MAP)


_EMOJI_CORE = (
    "\U0001f000-\U0001faff"  # pictographs, emoticons, transport, symbols, flags
    "☀-➿"  # misc symbols and dingbats (☀ ✅ ✔ ❤ ➡)
    "⬀-⯿"  # arrows and stars used as emoji (⬆ ⭐)
    "⌚⌛⏩-⏳⏸-⏺"  # ⌚ ⌛ ⏩ ⏰ ...
)
_EMOJI_MODIFIERS = "️⃣\U0001f3fb-\U0001f3ff"
EMOJI_RE = re.compile(
    rf"[{_EMOJI_CORE}][{_EMOJI_MODIFIERS}]*(?:‍[{_EMOJI_CORE}][{_EMOJI_MODIFIERS}]*)*"
)
# Symbols that are not emoji but still read as "decorated bullets" at the start of a line.
_BULLET_SYMBOL_RE = re.compile(r"^\s*[←-⇿■-◿➔-➿✓✔]")

HASHTAG_RE = re.compile(r"(?<![\w#&])#([A-Za-z][A-Za-z0-9_]*)")
_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*•–]|\d{1,2}[.)])\s+\S")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _is_hashtag_line(line: str) -> bool:
    stripped = HASHTAG_RE.sub("", line).strip()
    return bool(HASHTAG_RE.search(line)) and not stripped


def body_lines(text: str) -> list[str]:
    """Non-empty lines, excluding a trailing hashtag-only line."""
    lines = _lines(text)
    if lines and _is_hashtag_line(lines[-1]):
        lines = lines[:-1]
    return lines


def count_words(text: str) -> int:
    """Count words, ignoring hashtags and URLs."""
    cleaned = _URL_RE.sub(" ", HASHTAG_RE.sub(" ", normalize(text)))
    return len(_WORD_RE.findall(cleaned))


def opening_line(text: str) -> str:
    """The first non-empty line of a post."""
    lines = _lines(text)
    return lines[0].strip() if lines else ""


def has_bullets(text: str) -> bool:
    """True if any line is a bullet or numbered-list item."""
    return any(_BULLET_RE.match(line) for line in _lines(text))


def ends_with_question(text: str) -> bool:
    """True if the last line of prose (ignoring hashtags and a bare link) ends with '?'."""
    lines = [line for line in body_lines(text) if not _URL_RE.fullmatch(line.strip())]
    if not lines:
        return False
    last = EMOJI_RE.sub("", lines[-1]).rstrip()
    return last.endswith("?")


def sentences(text: str) -> list[str]:
    """Split prose into sentences. Each line (and each bullet) starts a new sentence."""
    out: list[str] = []
    for line in body_lines(normalize(text)):
        line = _BULLET_RE.sub(lambda m: m.group(0)[-1], line, count=1)
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
    return out


# --- Banned words and phrases -----------------------------------------------------------

_BANNED: list[tuple[str, str]] = [
    ("delve", r"\bdelv(?:e|es|ed|ing)\b"),
    ("dive in", r"\bdive in\b"),
    ("deep dive", r"\bdeep[- ]dives?\b"),
    ("let's dive", r"\blet's dive\b"),
    ("game-changer", r"\bgame[- ]changers?\b"),
    ("revolutionize", r"\brevolutioni[sz](?:e|es|ed|ing)\b"),
    ("unlock", r"\bunlock(?:s|ed|ing)?\b"),
    ("unleash", r"\bunleash(?:es|ed|ing)?\b"),
    ("harness", r"\bharness(?:es|ed|ing)?\b"),
    ("landscape", r"\blandscapes?\b"),
    ("realm", r"\brealms?\b"),
    ("tapestry", r"\btapestr(?:y|ies)\b"),
    ("paradigm", r"\bparadigms?\b"),
    ("synergy", r"\bsynerg(?:y|ies)\b"),
    ("seamless", r"\bseamless(?:ly)?\b"),
    ("robust", r"\brobust(?:ly|ness)?\b"),
    ("cutting-edge", r"\bcutting[- ]edge\b"),
    ("in today's fast-paced", r"\bin today's fast[- ]paced\b"),
    ("in today's world", r"\bin today's world\b"),
    ("in an era of", r"\bin an era of\b"),
    ("ever-evolving", r"\bever[- ]evolving\b"),
    ("ever-changing", r"\bever[- ]changing\b"),
    ("navigate the complexities", r"\bnavigat(?:e|es|ed|ing) the complexit(?:y|ies)\b"),
    ("it's important to note", r"\bit(?:'s| is) important to note\b"),
    ("it's worth noting", r"\bit(?:'s| is) worth noting\b"),
    ("at the end of the day", r"\bat the end of the day\b"),
    ("here's the thing", r"\bhere's the thing\b"),
    ("here's why", r"\bhere's why\b"),
    ("buckle up", r"\bbuckle up\b"),
    ("the future is here", r"\bthe future is here\b"),
    ("let that sink in", r"\blet that sink in\b"),
    # "elevated" is left out on purpose: "elevated permissions" is a normal security term.
    ("elevate", r"\belevat(?:e|es|ing)\b"),
    ("empower", r"\bempower(?:s|ed|ing|ment)?\b"),
    ("transformative", r"\btransformative\b"),
    ("supercharge", r"\bsupercharg(?:e|es|ed|ing)\b"),
    ("crucial role", r"\bcrucial roles?\b"),
    ("pivotal", r"\bpivotal\b"),
    ("testament", r"\btestament\b"),
    ("fostering", r"\bfostering\b"),
    ("moreover", r"\bmoreover\b"),
    ("furthermore", r"\bfurthermore\b"),
    ("in conclusion", r"\bin conclusion\b"),
    ("to summarize", r"\bto summari[sz]e\b"),
    ("key takeaways", r"\bkey takeaways?\b"),
    ("I'm excited to share", r"\b(?:i'm|i am) excited to share\b"),
    ("thrilled to announce", r"\bthrilled to announce\b"),
    ("agree?", r"\bagree\s*\?"),
    ("thoughts?", r"\bthoughts\s*\?"),
    ("what do you think?", r"\bwhat do you think\s*\?"),
    ("unpopular opinion", r"\bunpopular opinion\b"),
    ("final post", r"\bfinal post\b"),
    ("last post in this series", r"\blast post in (?:this|the) series\b"),
    ("wrapping up", r"\bwrapping up\b"),
    ("that's a wrap", r"\bthat's a wrap\b"),
    ("series recap", r"\bseries recap\b"),
]
_BANNED_RE = [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in _BANNED]

# Words that mark the next "leverage" as a noun ("more leverage", "have leverage").
_LEVERAGE_NOUN_CUES = {
    "the", "a", "an", "more", "less", "much", "some", "any", "no", "real", "enough", "little",
    "your", "our", "their", "my", "his", "her", "its", "of", "have", "has", "had", "having",
    "give", "gives", "gave", "gain", "gains", "gained", "without", "with", "own", "this",
    "that", "what", "negotiating", "bargaining", "financial",
}  # fmt: skip
_LEVERAGE_RE = re.compile(r"\b(leverag(?:e|es|ed|ing))\b", re.IGNORECASE)

_NUM = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_NUMBERING_RE = [
    re.compile(
        rf"\b(?:part|post|episode|chapter|entry|day|week|installment|edition)\s*#?\s*{_NUM}"
        rf"\s*(?:of|/)\s*{_NUM}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:part|post|episode|chapter|installment)\s*#\s*\d+\b", re.IGNORECASE),
    re.compile(rf"\bpart\s+{_NUM}\b(?!\s+of\s+(?:the|a|an|my|our|this|that|it)\b)", re.IGNORECASE),
    re.compile(r"\b\d+\s+of\s+\d+\b"),
    re.compile(r"\(\s*\d+\s*/\s*\d+\s*\)"),
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$", re.MULTILINE),
]

_HEDGE = r"(?:just\s+|only\s+|merely\s+|simply\s+|about\s+)?"
_NOT_X_ITS_Y_RE = [
    # "It's not X, it's Y" / "It's not just X, it's Y"
    re.compile(
        rf"\b(?:it's|it is|this is|that's|that is)\s+not\s+{_HEDGE}[^.!?\n]{{1,80}}?"
        r"[,;:—–]\s*(?:it's|it is|this is|that's|that is)\b",
        re.IGNORECASE,
    ),
    # "This isn't X. It's Y." / "AI isn't magic, it's plumbing." / "Agents aren't X. They're Y."
    re.compile(
        rf"\b\w+\s+(?:isn't|is not|wasn't|was not|aren't|are not)\s+{_HEDGE}[^.!?\n]{{1,80}}?"
        r"[.,;:—–]\s*(?:it's|it is|it was|this is|that's|they're|they are)\b",
        re.IGNORECASE,
    ),
]

_ITEM = r"[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2}"
_SHORT = r"[A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*)?"
_TRIAD_RE = [
    # "faster, cheaper, and better" / "faster, cheaper or better"
    re.compile(rf"(?<!, )(?<![\w,])(?:{_ITEM}),\s+(?:{_ITEM}),?\s+(?:and|or)\s+(?:{_ITEM})\b"),
    # "Faster, cheaper, better."
    re.compile(rf"(?<!, )(?<![\w,])(?:{_SHORT}),\s+(?:{_SHORT}),\s+(?:{_SHORT})(?=\s*[.!?]|\s*$)"),
]

_FORMATTING_RE = [
    ("bold markdown (**text**)", re.compile(r"\*\*[^*\n]+\*\*")),
    ("bold markdown (__text__)", re.compile(r"__[^_\n]+__")),
    ("markdown heading", re.compile(r"^\s*#{1,6}\s+\S", re.MULTILINE)),
    ("unicode bold/italic letters", re.compile(r"[\U0001d400-\U0001d7ff]")),
]

# Small closed-class words keep their identity in an opening-line "shape"; everything else
# collapses to W (word) or N (number), so "Most AI demos die in week two." and
# "Most agent pilots stall in month three." share a shape.
_FUNCTION_WORDS = {
    "a", "an", "the", "i", "we", "you", "it", "this", "that", "these", "those", "is", "are",
    "was", "were", "be", "been", "am", "of", "in", "on", "at", "to", "for", "with", "by",
    "from", "about", "as", "and", "or", "but", "not", "no", "my", "our", "your", "their", "its",
    "they", "he", "she", "me", "us", "them", "what", "why", "how", "when", "where", "who",
    "which", "if", "so", "do", "does", "did", "have", "has", "had", "can", "will", "would",
    "should", "could", "every", "most", "all", "some", "one", "there", "here", "just", "still",
    "than", "then", "more", "less", "last",
}  # fmt: skip


def opening_shape(line: str) -> str:
    """Reduce an opening line to its structural shape for repeat detection."""
    tokens = re.findall(r"[A-Za-z0-9']+|[?!.:,]", normalize(line).lower())
    shape = []
    for token in tokens:
        if token in _FUNCTION_WORDS or not token[0].isalnum():
            shape.append(token)
        elif token.isdigit():
            shape.append("N")
        else:
            shape.append("W")
    return " ".join(shape)


def _first_words(line: str, n: int = 3) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9']+", normalize(line).lower())[:n])


# --- Individual checks --------------------------------------------------------------------


def _check_length(text: str, limits: LintLimits) -> tuple[int, list[LintIssue]]:
    words = count_words(text)
    if words < limits.min_words:
        return words, [LintIssue("length", f"{words} words; minimum is {limits.min_words}.")]
    if words > limits.max_words:
        return words, [LintIssue("length", f"{words} words; maximum is {limits.max_words}.")]
    return words, []


def _check_banned(text: str) -> list[LintIssue]:
    issues = []
    for label, pattern in _BANNED_RE:
        match = pattern.search(text)
        if match:
            issues.append(
                LintIssue("banned_phrase", f'Banned phrase "{label}": "{match.group(0)}".')
            )
    for match in _LEVERAGE_RE.finditer(text):
        word = match.group(1).lower()
        before = re.findall(r"[a-z']+", text[: match.start()].lower())
        is_noun = word == "leverage" and bool(before) and before[-1] in _LEVERAGE_NOUN_CUES
        if not is_noun:
            issues.append(
                LintIssue("banned_phrase", f'"leverage" used as a verb: "{match.group(0)}".')
            )
            break
    for pattern in _NUMBERING_RE:
        match = pattern.search(text)
        if match:
            issues.append(
                LintIssue("series_numbering", f'Posts must not be numbered: "{match.group(0)}".')
            )
            break
    return issues


def _check_em_dashes(text: str, limits: LintLimits) -> list[LintIssue]:
    count = text.count("—")
    if count > limits.max_em_dashes:
        return [LintIssue("em_dash", f"{count} em dashes; maximum is {limits.max_em_dashes}.")]
    return []


def _check_not_x_its_y(text: str) -> list[LintIssue]:
    for pattern in _NOT_X_ITS_Y_RE:
        match = pattern.search(text)
        if match:
            return [
                LintIssue(
                    "not_x_its_y", f'"It\'s not X, it\'s Y" construction: "{match.group(0)}".'
                )
            ]
    return []


def _check_sentence_starts(text: str) -> list[LintIssue]:
    firsts = []
    for sentence in sentences(text):
        match = re.match(r"[\"'(\[]*([A-Za-z0-9']+)", sentence)
        firsts.append(match.group(1).lower() if match else "")
    run = 1
    for prev, cur in itertools.pairwise(firsts):
        run = run + 1 if cur and cur == prev else 1
        if run >= 3:
            return [
                LintIssue(
                    "repeated_sentence_start",
                    f'Three or more consecutive sentences start with "{cur}".',
                )
            ]
    return []


def _check_emoji(text: str, limits: LintLimits) -> list[LintIssue]:
    issues = []
    count = len(EMOJI_RE.findall(text))
    if count > limits.max_emojis:
        issues.append(LintIssue("emoji_count", f"{count} emojis; maximum is {limits.max_emojis}."))
    for line in _lines(text):
        if EMOJI_RE.search(line) and not EMOJI_RE.sub("", line).strip():
            issues.append(LintIssue("emoji_line", "A line contains only emoji."))
            break
    for line in _lines(text):
        if EMOJI_RE.match(line.lstrip()) or _BULLET_SYMBOL_RE.match(line):
            issues.append(
                LintIssue(
                    "emoji_bullet", f'Emoji or arrow used as a bullet: "{line.strip()[:40]}".'
                )
            )
            break
    return issues


def _check_hashtags(text: str, limits: LintLimits) -> list[LintIssue]:
    issues = []
    tags = HASHTAG_RE.findall(text)
    if len(tags) > limits.max_hashtags:
        issues.append(
            LintIssue("hashtag_count", f"{len(tags)} hashtags; maximum is {limits.max_hashtags}.")
        )
    lines = _lines(text)
    if any(HASHTAG_RE.search(line) for line in lines[:-1]):
        issues.append(LintIssue("hashtag_placement", "Hashtags must only appear on the last line."))
    return issues


def _check_triads(text: str) -> list[LintIssue]:
    found: list[str] = []
    for sentence in sentences(text):
        for pattern in _TRIAD_RE:
            found.extend(m.group(0) for m in pattern.finditer(sentence))
    if len(found) > 1:
        quoted = "; ".join(f'"{f}"' for f in found[:3])
        return [
            LintIssue("triad", f"Lists of three parallel phrases appear more than once: {quoted}.")
        ]
    return []


def _check_formatting(text: str) -> list[LintIssue]:
    for label, pattern in _FORMATTING_RE:
        if pattern.search(text):
            return [LintIssue("formatting", f"No headings or bold text ({label}).")]
    return []


def _check_links(text: str, limits: LintLimits) -> list[LintIssue]:
    count = len(_URL_RE.findall(text))
    if count > limits.max_links:
        return [LintIssue("links", f"{count} links; maximum is {limits.max_links}.")]
    return []


def _check_opening(text: str, recent_texts: Sequence[str]) -> list[LintIssue]:
    issues = []
    first = opening_line(text)
    if re.match(r"^\W*(ever wondered|have you ever)\b", normalize(first), re.IGNORECASE):
        issues.append(
            LintIssue(
                "opening_rhetorical", 'Opening line starts with "Ever wondered"/"Have you ever".'
            )
        )
    shape, words = opening_shape(first), _first_words(first)
    for previous in recent_texts[:5]:
        prev_first = opening_line(previous)
        if not prev_first:
            continue
        if opening_shape(prev_first) == shape or (
            len(words) == 3 and _first_words(prev_first) == words
        ):
            issues.append(
                LintIssue(
                    "opening_repeat",
                    f'Opening line has the same structure as a recent post: "{prev_first[:80]}".',
                )
            )
            break
    return issues


def _check_bullets(text: str, recent_texts: Sequence[str], limits: LintLimits) -> list[LintIssue]:
    bullet_lines = [line for line in _lines(text) if _BULLET_RE.match(line)]
    if not bullet_lines:
        return []
    issues = []
    if len(bullet_lines) > limits.max_bullets:
        issues.append(
            LintIssue("bullets", f"{len(bullet_lines)} bullets; maximum is {limits.max_bullets}.")
        )
    if any(has_bullets(previous) for previous in recent_texts[:2]):
        issues.append(
            LintIssue(
                "bullets_frequency",
                "Bullets are allowed in at most 1 of every 3 posts and a recent post used them.",
            )
        )
    return issues


def _check_ending_question(text: str, recent_texts: Sequence[str]) -> list[LintIssue]:
    if ends_with_question(text) and recent_texts and ends_with_question(recent_texts[0]):
        return [
            LintIssue(
                "ending_question",
                "The previous post ended with a question; at most half of posts may.",
            )
        ]
    return []


def lint_post(
    text: str,
    limits: LintLimits | None = None,
    recent_texts: Sequence[str] = (),
) -> LintResult:
    """Run every check on `text`.

    `recent_texts` are earlier published posts, most recent first. They drive the rules that
    depend on history: opening-line repeats (last 5), bullet frequency (last 2) and how often
    posts end with a question (last 1).
    """
    limits = limits or LintLimits()
    norm = normalize(text)
    recent = [normalize(t) for t in recent_texts]
    words, issues = _check_length(norm, limits)
    issues += _check_banned(norm)
    issues += _check_em_dashes(norm, limits)
    issues += _check_not_x_its_y(norm)
    issues += _check_sentence_starts(norm)
    issues += _check_emoji(norm, limits)
    issues += _check_hashtags(norm, limits)
    issues += _check_triads(norm)
    issues += _check_formatting(norm)
    issues += _check_links(norm, limits)
    issues += _check_opening(norm, recent)
    issues += _check_bullets(norm, recent, limits)
    issues += _check_ending_question(norm, recent)
    return LintResult(issues=issues, word_count=words)
