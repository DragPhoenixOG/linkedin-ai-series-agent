This post is nearly ready. It only needs the problems below fixed.

<post>
$post
</post>

The post has $words words. When you're done it must still have between $min_words and
$max_words.

Problems:

$problems

Fix each problem by rewriting only the sentence it is in (or removing it). Every other sentence
stays exactly as it is.
- A sentence that sounds like AI: say what it means the way you would to a colleague, plainer and
  more specific. Don't swap one slogan for another. If the post doesn't need the sentence, remove
  it, as long as the post stays long enough.
- Too short: pick one sentence and replace it with itself plus one or two new sentences that add
  a concrete point from the research. Too long: shorten or remove a sentence.
- Repeated sentence starts: reword the start of one of them.

Keep the meaning and the tone. The new wording must not break any other rule: no "isn't A. It's
B" setups, no em dashes, no new lists of three, no new personal stories, observations or numbers.

Reply with only this JSON:

{"edits": [{"find": "the exact sentence from the post, copied character for character",
            "replace": "its new wording, or an empty string to remove it"}]}
