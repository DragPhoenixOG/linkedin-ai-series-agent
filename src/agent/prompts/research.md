Today is $today. I'm writing a LinkedIn post on this topic:

Topic: $title
Angle: $angle

Use web search to find 1 or 2 recent, credible developments related to this topic, published in
the last $lookback_days days (on or after $since). Good finds: a product launch or GA release, a
new version of a framework or protocol, an enterprise rollout with concrete details, a published
incident or postmortem, new guidance from a standards body or major vendor.

Rules:
- Prefer original sources (company blogs, official docs and announcements, standards bodies,
  reputable tech press) over aggregators, SEO blogs and social posts.
- Only include something you found in this session's search results, with its URL.
- Check the publication date. Skip anything published before $since.
- Skip rumours, leaks and speculation.
- Describe what was actually released or said. Don't overstate capabilities.
- If nothing solid turns up, return an empty list. That is a fine outcome.

When you are done searching, reply with only this JSON inside <result></result> tags:

<result>
{"developments": [
  {"summary": "One or two plain sentences: what happened, who did it.",
   "date": "YYYY-MM-DD",
   "why_it_matters": "One sentence on why it matters for engineers or enterprise reliability.",
   "sources": [{"url": "https://...", "title": "Page title", "publisher": "Who published it"}]}
]}
</result>
