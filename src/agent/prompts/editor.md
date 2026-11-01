Edit this LinkedIn draft by $author_first_name. Today is $today.

Post type: $post_type
Tone: $tone
Format: $format_name. $format_description
Subject: $subject_line

## Research the post may rely on

Claims about recent events must match this. Anything else about recent events must go,
including specifics about these companies or products (what they built, how it works, who uses
it, examples, numbers) that aren't in the summary or the listed details.

$research

## Recent posts (the new one must not repeat them)

$recent_posts

## Draft

<draft>
$draft
</draft>

## Automated checks (exact, done by code)

$checks

Code checks length, em dashes, hashtags, emoji, links, banned words and the banned patterns
(contrast setups, lists of three, repeated sentence starts). Don't count or report those
yourself; the writer gets the code's results directly.

## Personal claims to check

$personal_claims

## Numbers to check

$numbers

## Dates in the research, counted from today

Weeks start on Monday. A post that says "last week" for something from earlier this week (or the
other way round) has a fact problem.
$research_dates

## What to do

1. human_score: score the draft 1-10 on "sounds like a real person wrote it".
2. ai_sounding: list every sentence that sounds like AI, corporate or generic, each with a short
   reason. Quote the sentence exactly.
3. fact_problems: list claims not supported by the research or the author facts (invented
   numbers, overstated product capabilities, made-up personal stories, rumours stated as fact,
   wrong dates such as "last week" for something from yesterday). Start each item with the
   exact sentence in quotes, then say why. Before listing a sentence, check it against the
   author facts: the author's own $author_own_work is in there and is fine.
   Check every sentence with "I", "we", "our" or "my": if what it says $author_first_name or their team did
   isn't in the author facts, it's a made-up personal story. List it. That includes the
   industry "we" ("We spent two years...", "we keep building..."): readers take it as the author's
   own story. So does what the author has seen, watched or tested ("I've watched teams...", "most teams I
   see", "I spent last month..."). First-person opinions ("I think...") are fine. A number
   presented as real (a statistic, or a figure about a company, the industry or the author) must
   come from the research or author facts; so must one in an example that isn't marked as made
   up ("drops 2% of SKUs").
   Not fact problems, so never list these: opinions and predictions ("I think...", "the hard
   part is..."), general truths about software or business ("a wrong order costs money"), the
   post's own topic or argument, and scenarios opened as made up ("Picture...", "Say...",
   "Imagine..."), small details inside them included, and figures of speech like "a thousand
   moving parts". Weak or generic lines like these belong in ai_sounding, not here.
   rule_problems: only the rules code can't check: the humour hard limits (even in a straight
   post, for example a joke at the expense of interns or junior people), anything implying the
   series is ending or numbering posts, and naming clients or brands from the author's work. The format
   above was chosen on purpose, so don't flag it.
   Both lists hold problems only. If there are none, return an empty list. Never add notes that
   something is fine, allowed or correctly cited.
   These lists are what the writer fixes, so make them complete: anything you don't list stays.
4. $humour_instruction
5. verdict: one blunt sentence on the draft.
6. rewrite: your improved version, kept for reference only (it is never posted). Fix every item
   you listed, keep all facts accurate, and don't add new facts, numbers, links or personal
   stories. Plain text, no markdown.
$constraints
