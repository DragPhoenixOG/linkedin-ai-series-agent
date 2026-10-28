Today is $today. Before anyone writes about this story, check that it is true and correctly
described.

Story: $title (reported $date)
Summary: $summary

Key facts to check:
$facts

Sources found so far:
$sources

Use web search to check each key fact against at least 2 independent sources, or against 1
official source (the company, project or body the story is about). Read what the sources
actually say; don't rely on headlines.

- "supported": true only if the fact is clearly stated by those sources.
- status: "confirmed" if the story happened as described; "unconfirmed" if it rests on leaks,
  "reportedly", anonymous sources or is still a rumour; "false" if sources contradict it.
- verified: true only if status is "confirmed" and every key fact is supported.
- summary: one or two plain sentences of what verifiably happened, corrected if needed.

When you are done, reply with only this JSON inside <result></result> tags:

<result>
{"verified": true,
 "status": "confirmed",
 "facts": [{"fact": "...", "supported": true, "sources": ["https://..."]}],
 "summary": "...",
 "sources": [{"url": "https://...", "title": "Page title", "publisher": "Publisher", "official": true}]}
</result>
