Edit this LinkedIn draft by Ashish.

Post type: $post_type
Tone: $tone
Format: $format_name. $format_description
Subject: $subject_line

## Research the post may rely on

Claims about recent events must match this. Anything else about recent events must go.

$research

## Recent posts (the new one must not repeat them)

$recent_posts

## Draft

<draft>
$draft
</draft>

## What to do

1. human_score: score the draft 1-10 on "sounds like a real person wrote it".
2. ai_sounding: list every sentence that sounds like AI, corporate or generic, each with a short
   reason. Quote the sentence exactly.
3. fact_problems: list claims not supported by the research or the author facts (invented
   numbers, overstated product capabilities, made-up personal stories, rumours stated as fact).
   Check every sentence with "I", "we", "our" or "my": if what it says Ashish or his team did
   isn't in the author facts, it's a made-up personal story. List it. First-person opinions
   ("I think...") are fine.
   rule_problems: list anything that breaks the style guide (banned words or patterns, length,
   hashtags, emoji, numbering, implying the series ends), including the humour hard limits even
   in a straight post (for example a joke at the expense of interns or junior people).
4. $humour_instruction
5. verdict: one blunt sentence on the draft.
6. rewrite: the improved post. It must fix every item you listed in ai_sounding, fact_problems
   and rule_problems: rewrite or remove each one, don't just reword it slightly. Turn made-up
   personal stories into general observations or clearly hypothetical scenarios. Cut filler,
   replace abstractions with specifics, break the rhythm, remove anything that sounds like a
   slogan, keep all facts accurate. Keep the format and tone. Keep it $min_words to $max_words
   words, with 0 to $max_hashtags hashtags on the last line only. Don't add new facts, numbers,
   links or personal stories. Plain text, no markdown, a blank line between paragraphs.
$constraints
