Today is $today. I'm writing a LinkedIn post on this topic:

Topic: $title
Angle: $angle

Below are recent articles (published on or after $since), each with an id like [A1], where it was
published, and an excerpt of its text.

Pick 1 or 2 credible, recent developments from these articles that relate to the topic and would
ground the post: a launch or GA release, a new version of a framework or protocol, an enterprise
rollout with concrete details, a published incident or postmortem, new guidance from a standards
body or major vendor.

Rules:
- Use only facts stated in the excerpts below. Nothing from memory.
- Prefer official sources (marked "official") and articles that state concrete facts.
- Skip rumours, leaks, speculation, opinion pieces and anything only loosely related.
- Describe what was actually released or said. Don't overstate capabilities.
- If nothing here is solid and relevant, return an empty list. That is a fine outcome.

Articles:

$articles

Reply with only this JSON inside <result></result> tags:

For each development also give up to 4 "details": specific facts the excerpt states, such as
what exactly was built or released, how it works, who uses it, names and numbers. The post may
use only these specifics, so pick the ones a writer would want, and copy nothing the text
doesn't say.

<result>
{"developments": [
  {"summary": "One or two plain sentences: what happened, who did it.",
   "why_it_matters": "One sentence on why it matters for engineers or enterprise reliability.",
   "details": ["A specific fact the article states.", "Another one."],
   "article_ids": ["A1"]}
]}
</result>
