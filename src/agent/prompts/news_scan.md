Today is $today. Below are tech and AI headlines from the last $lookback_days days, each with an id
like [A1], where it was published, the date, and a short summary when the feed had one.

Pick 5 to 10 candidate stories that an engineer writing about AI and enterprise reliability might
react to on LinkedIn: model launches, new agent frameworks or protocols, big enterprise AI
rollouts, outages or AI failures with lessons, funding or acquisitions that shift the market,
regulation that affects how companies build with AI, developer tooling changes, and
commerce/retail tech meeting AI.

Rules:
- Use only what the headlines and summaries say. Nothing from memory.
- Several headlines may cover the same story: list all their ids together in headline_ids.
  A story needs either 2 headlines from different sites, or 1 marked "official".
- Mark rumours, leaks, "reportedly" stories and speculation as "confirmed": false.
- Skip stories already covered in earlier posts:
$covered

For each candidate, rate:
- relevance (1-5): fit with agentic AI, AI engineering, enterprise reliability, AI careers,
  integration and commerce tech.
- angle_strength (1-5): how much a working engineer/consultant has a real, specific take on it
  (reliability, hype vs reality, what it changes for builders), beyond restating the news.
- significance: "major" only for a genuinely big story (a major model launch, a major AI
  incident, a market-shifting deal or regulation). Otherwise "normal".

Headlines:

$headlines

Reply with only this JSON inside <result></result> tags:

<result>
{"candidates": [
  {"title": "Short factual headline in your own words",
   "summary": "One or two plain sentences: what happened, who.",
   "key_facts": ["Each concrete claim a post would rely on, one per item"],
   "headline_ids": ["A3", "A7"],
   "confirmed": true,
   "significance": "normal",
   "relevance": 4,
   "angle_strength": 3,
   "angle": "One sentence: the take worth sharing."}
]}
</result>
