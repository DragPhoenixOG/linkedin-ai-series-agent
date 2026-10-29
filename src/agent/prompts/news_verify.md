Today is $today. Before anyone writes about this story, check that it is true and correctly
described, using only the article text below.

Story: $title (first reported $date)
Summary: $summary

Key facts to check:
$facts

Articles (each with an id like [A1] and an excerpt of its text):

$articles

For each key fact:
- "supported": true only if the article text clearly states it. Headlines alone don't count.
- "article_ids": the ids of the articles that state it.

Then:
- status: "confirmed" if the story happened as described; "unconfirmed" if it rests on leaks,
  "reportedly", anonymous sources or is still a rumour; "false" if the articles contradict it.
- verified: true only if status is "confirmed" and every key fact is supported.
- summary: one or two plain sentences of what verifiably happened, corrected if needed, in your
  own words.
- details: up to 4 specific facts the article text states, such as what exactly was built or
  released, how it works, who uses it, names and numbers. The post may use only these
  specifics, so pick the ones a writer would want, and copy nothing the text doesn't say.

Reply with only this JSON inside <result></result> tags:

<result>
{"verified": true,
 "status": "confirmed",
 "facts": [{"fact": "...", "supported": true, "article_ids": ["A1"]}],
 "summary": "...",
 "details": ["...", "..."]}
</result>
