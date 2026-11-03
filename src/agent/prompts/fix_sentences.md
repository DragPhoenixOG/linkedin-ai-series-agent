This post is nearly ready. It only needs the problems below fixed.

<post>
$post
</post>

The post has $words words. When you're done it must have between $min_words and $max_words.

Problems:

$problems

Fix each problem by rewriting only the sentence it is in (or removing it). Every other sentence
stays exactly as it is.
- Too short: pick one sentence and replace it with itself plus one or two new sentences that add
  a concrete point from the research. Too long: shorten or remove a sentence.
- Repeated sentence starts: reword the start of one of them.
- An "isn't A. It's B" setup: say B directly in its own words. Never just delete the "not": that
  flips the meaning.
- An unsupported claim: remove the sentence, or rewrite it so it claims nothing the research or
  author facts don't back up.

Keep the meaning and the tone. The new wording must not break any other rule: no "isn't A. It's
B" setups, no em dashes, no new lists of three, no new personal stories, observations or numbers.

Reply with only this JSON:

{"edits": [{"find": "the exact sentence from the post, copied character for character",
            "replace": "its new wording, or an empty string to remove it"}]}
