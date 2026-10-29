"""Pass and fail cases for every lint rule."""

from __future__ import annotations

import pytest

from agent.lint import (
    LintLimits,
    count_words,
    ends_with_question,
    has_bullets,
    lint_post,
    opening_shape,
)

FILLER = (
    "Most of the work is ordinary engineering that nobody puts on a slide, and that is fine "
    "because ordinary engineering is what keeps orders moving when the clever part is wrong. "
)


def rules(text: str, recent: list[str] | None = None, limits: LintLimits | None = None) -> set[str]:
    return {issue.rule for issue in lint_post(text, limits, recent or []).issues}


def with_line(post: str, line: str) -> str:
    """Insert a paragraph just before the hashtag line."""
    body, tags = post.rsplit("\n\n", 1)
    return f"{body}\n\n{line}\n\n{tags}"


def test_clean_post_passes(clean_post: str) -> None:
    result = lint_post(clean_post)
    assert result.passed, result.summary()
    assert "lint passed" in result.summary()


# --- length ----------------------------------------------------------------------------


def test_too_short_fails() -> None:
    assert "length" in rules("Agents need guardrails. That is the whole post.\n\n#AI")


def test_too_long_fails(clean_post: str) -> None:
    assert "length" in rules(with_line(clean_post, FILLER * 4))


def test_word_count_ignores_hashtags_and_links() -> None:
    assert count_words("One two three https://example.com/a-b-c\n\n#AI #Agents") == 3


# --- banned phrases --------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "We should delve into this.",
        "Let's dive in.",
        "Time for a deep dive.",
        "This is a game changer for retail.",
        "It will revolutionise support.",
        "It unlocks new value.",
        "Teams can harness the model.",
        "The AI landscape moved fast.",
        "Our systems are robust.",
        "It runs seamlessly across tools.",
        "This is cutting-edge stuff.",
        "In today’s fast-paced market, speed wins.",
        "It’s worth noting that costs rose.",
        "At the end of the day, people decide.",
        "Here’s the thing about agents.",
        "Let that sink in.",
        "We want to empower teams.",
        "Moreover, the logs were empty.",
        "In conclusion, test more.",
        "Key takeaways from the week.",
        "I'm excited to share some news.",
        "Agree?",
        "Thoughts?",
        "What do you think?",
        "Unpopular opinion: tests matter.",
        "This is my final post on agents.",
        "Wrapping up the series now.",
        "That’s a wrap.",
        "It plays a crucial role in trust.",
        "A pivotal moment for tooling.",
        "It is a testament to the team.",
    ],
)
def test_banned_phrases_fail(clean_post: str, phrase: str) -> None:
    assert "banned_phrase" in rules(with_line(clean_post, phrase))


def test_banned_phrases_are_case_insensitive(clean_post: str) -> None:
    assert "banned_phrase" in rules(with_line(clean_post, "DELVE deeper, they said."))


def test_elevated_permissions_is_allowed(clean_post: str) -> None:
    assert "banned_phrase" not in rules(
        with_line(clean_post, "The agent ran with elevated permissions.")
    )


@pytest.mark.parametrize(
    "sentence",
    ["We leverage MCP for tools.", "Teams are leveraging agents.", "You can leverage the logs."],
)
def test_leverage_as_verb_fails(clean_post: str, sentence: str) -> None:
    assert "banned_phrase" in rules(with_line(clean_post, sentence))


@pytest.mark.parametrize(
    "sentence",
    [
        "Domain knowledge gives you more leverage than a new framework.",
        "Clients have leverage here.",
    ],
)
def test_leverage_as_noun_passes(clean_post: str, sentence: str) -> None:
    assert "banned_phrase" not in rules(with_line(clean_post, sentence))


@pytest.mark.parametrize(
    "sentence",
    [
        "This is part 3 of 9.",
        "Post #4 in the series.",
        "Part two: agents.",
        "(3/9)",
        "Day 4 of 30.",
    ],
)
def test_series_numbering_fails(clean_post: str, sentence: str) -> None:
    assert "series_numbering" in rules(with_line(clean_post, sentence))


def test_part_of_the_problem_is_not_numbering(clean_post: str) -> None:
    assert "series_numbering" not in rules(with_line(clean_post, "That is part of the problem."))


# --- em dashes ---------------------------------------------------------------------------


def test_one_em_dash_passes(clean_post: str) -> None:
    assert "em_dash" not in rules(with_line(clean_post, "Logs help — when someone reads them."))


def test_two_em_dashes_fail(clean_post: str) -> None:
    line = "Logs help — when someone reads them — which is rare."
    assert "em_dash" in rules(with_line(clean_post, line))


# --- "It's not X, it's Y" ----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "It's not a model problem, it's a data problem.",
        "It’s not just about speed, it’s about trust.",
        "This isn't a demo. It's a system.",
        "AI isn't magic. It's plumbing.",
        "Agents aren't coworkers. They're software.",
    ],
)
def test_not_x_its_y_fails(clean_post: str, sentence: str) -> None:
    assert "not_x_its_y" in rules(with_line(clean_post, sentence))


def test_plain_negation_passes(clean_post: str) -> None:
    line = "The model is not the hard part for most teams I talk to."
    assert "not_x_its_y" not in rules(with_line(clean_post, line))


# --- repeated sentence starts ------------------------------------------------------------


def test_three_sentences_same_start_fail(clean_post: str) -> None:
    line = "We tried retries. We tried queues. We tried prayer."
    assert "repeated_sentence_start" in rules(with_line(clean_post, line))


def test_two_sentences_same_start_pass(clean_post: str) -> None:
    line = "We tried retries. We tried queues. Then it worked."
    assert "repeated_sentence_start" not in rules(with_line(clean_post, line))


# --- emoji -------------------------------------------------------------------------------


def test_one_emoji_passes(clean_post: str) -> None:
    assert not {"emoji_count", "emoji_line", "emoji_bullet"} & rules(
        with_line(clean_post, "That one still makes me laugh \U0001f605")
    )


def test_two_emojis_fail(clean_post: str) -> None:
    assert "emoji_count" in rules(with_line(clean_post, "Ship it \U0001f680 and pray \U0001f64f"))


def test_zwj_sequence_counts_as_one_emoji(clean_post: str) -> None:
    assert "emoji_count" not in rules(with_line(clean_post, "Me debugging \U0001f9d1‍\U0001f4bb"))


def test_emoji_only_line_fails(clean_post: str) -> None:
    assert "emoji_line" in rules(with_line(clean_post, "\U0001f680"))


@pytest.mark.parametrize("marker", ["✅", "\U0001f50d", "➡️", "→"])
def test_emoji_bullets_fail(clean_post: str, marker: str) -> None:
    assert "emoji_bullet" in rules(with_line(clean_post, f"{marker} Check the logs first"))


# --- hashtags ------------------------------------------------------------------------------


def test_three_hashtags_pass(clean_post: str) -> None:
    post = clean_post.replace("#AgenticAI #EnterpriseAI", "#AgenticAI #EnterpriseAI #Reliability")
    assert not {"hashtag_count", "hashtag_placement"} & rules(post)


def test_four_hashtags_fail(clean_post: str) -> None:
    post = clean_post.replace("#AgenticAI #EnterpriseAI", "#A #B #C #D")
    assert "hashtag_count" in rules(post)


def test_hashtag_in_body_fails(clean_post: str) -> None:
    assert "hashtag_placement" in rules(with_line(clean_post, "We shipped the #MCP server."))


def test_csharp_is_not_a_hashtag(clean_post: str) -> None:
    assert "hashtag_placement" not in rules(with_line(clean_post, "Half the backend is C# anyway."))


# --- triads --------------------------------------------------------------------------------


def test_one_triad_passes(clean_post: str) -> None:
    line = "Products, stock and orders all moved through the same queue."
    assert "triad" not in rules(with_line(clean_post, line))


def test_one_oxford_comma_triad_counts_once(clean_post: str) -> None:
    line = "You can handle permissions, VPCs, and observability."
    assert "triad" not in rules(with_line(clean_post, line))


def test_two_triads_fail(clean_post: str) -> None:
    line = "It was faster, cheaper and simpler. Teams wanted retries, timeouts, and alerts."
    assert "triad" in rules(with_line(clean_post, line))


def test_asyndetic_triads_count(clean_post: str) -> None:
    line = "Faster, cheaper, better. Smaller, safer, calmer."
    assert "triad" in rules(with_line(clean_post, line))


def test_four_item_list_is_not_a_triad(clean_post: str) -> None:
    line = "We sync products, stock, orders, and returns. Then retries, logs and alerts."
    assert "triad" not in rules(with_line(clean_post, line))


# --- formatting and links ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "**Lesson one** matters.",
        "## What I learned",
        "\U0001d5d5\U0001d5fc\U0001d5f9\U0001d5f1 text",
    ],
)
def test_bold_and_headings_fail(clean_post: str, line: str) -> None:
    assert "formatting" in rules(with_line(clean_post, line))


def test_one_link_passes(clean_post: str) -> None:
    assert "links" not in rules(with_line(clean_post, "Source: https://example.com/news"))


def test_two_links_fail(clean_post: str) -> None:
    line = "Sources: https://example.com/a and https://example.org/b"
    assert "links" in rules(with_line(clean_post, line))


# --- opening line ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "opening", ["Ever wondered why agents fail?", "Have you ever shipped a demo twice?"]
)
def test_rhetorical_opening_fails(clean_post: str, opening: str) -> None:
    post = opening + "\n\n" + clean_post.split("\n\n", 1)[1]
    assert "opening_rhetorical" in rules(post)


def test_opening_with_same_shape_as_recent_post_fails(clean_post: str) -> None:
    previous = 'Last week a stock sync failed because two systems disagreed on what "available" meant.\n\nMore text.'
    post = clean_post.replace(
        'Last month a returns flow broke because two systems disagreed on what "cancelled" meant.',
        'Last month a refund job broke because two services disagreed on what "settled" meant.',
    )
    assert "opening_repeat" in rules(post, recent=[previous])


def test_opening_only_checks_last_five_posts(clean_post: str) -> None:
    same_shape = clean_post
    others = [f"Totally different opener number {i}.\n\nBody." for i in range(5)]
    assert "opening_repeat" not in rules(clean_post, recent=[*others, same_shape])
    assert "opening_repeat" in rules(clean_post, recent=[same_shape, *others])


def test_opening_shape_collapses_content_words() -> None:
    assert opening_shape("Most AI demos die in week two.") == opening_shape(
        "Most agent pilots stall in week three."
    )
    assert opening_shape("Most AI demos die in week two.") != opening_shape(
        "I deleted half our prompts yesterday."
    )


# --- bullets -------------------------------------------------------------------------------------


def test_four_bullets_pass(clean_post: str) -> None:
    bullets = "- logs\n- retries\n- alerts\n- owners"
    assert not {"bullets", "bullets_frequency"} & rules(with_line(clean_post, bullets))


def test_five_bullets_fail(clean_post: str) -> None:
    bullets = "- logs\n- retries\n- alerts\n- owners\n- runbooks"
    assert "bullets" in rules(with_line(clean_post, bullets))


def test_bullets_fail_if_a_recent_post_used_them(clean_post: str) -> None:
    bullets = "- logs\n- retries"
    previous_with_bullets = "Opening.\n\n- one\n- two\n\n#AI"
    assert "bullets_frequency" in rules(
        with_line(clean_post, bullets), recent=["Plain.", previous_with_bullets]
    )
    assert "bullets_frequency" not in rules(
        with_line(clean_post, bullets), recent=["Plain.", "Plain too.", previous_with_bullets]
    )


def test_has_bullets_detects_numbered_lists() -> None:
    assert has_bullets("Intro\n1. first\n2. second")
    assert not has_bullets("Intro\nNo list here.")


# --- ending questions -----------------------------------------------------------------------------


def test_question_ending_detected_before_hashtags() -> None:
    assert ends_with_question("Body.\n\nWould you trust it?\n\n#AI")
    assert not ends_with_question("Body.\n\nI would not.\n\n#AI")


def test_two_question_endings_in_a_row_fail(clean_post: str) -> None:
    post = with_line(clean_post, "Would you let it touch refunds?")
    assert "ending_question" in rules(post, recent=["Earlier post.\n\nDo you agree with me here?"])
    assert "ending_question" not in rules(post, recent=["Earlier post.\n\nI don't."])
