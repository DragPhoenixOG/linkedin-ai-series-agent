Today is $today. Find tech and AI news from the last $lookback_days days (published on or after
$since) that an engineer writing about AI and enterprise reliability might react to on LinkedIn.

Look for: model launches, new agent frameworks or protocols, big enterprise AI rollouts, outages
or AI failures with lessons, funding or acquisitions that shift the market, regulation that
affects how companies build with AI, developer tooling changes, and commerce/retail tech meeting
AI.

Collect 5 to 10 candidate stories. Rules:
- Prefer original sources: company blogs, official announcements, docs, regulators, and
  reputable tech press. Avoid aggregators, SEO rewrites and social posts as the main source.
- Give at least 2 sources from different sites for each story, or 1 official source (the company
  or body the news is about). Mark official sources with "official": true.
- Check each publication date. Skip anything published before $since.
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

When you are done searching, reply with only this JSON inside <result></result> tags:

<result>
{"candidates": [
  {"title": "Short factual headline in your own words",
   "summary": "One or two plain sentences: what happened, who, when.",
   "date": "YYYY-MM-DD",
   "key_facts": ["Each concrete claim a post would rely on, one per item"],
   "sources": [{"url": "https://...", "title": "Page title", "publisher": "Publisher", "official": false}],
   "confirmed": true,
   "significance": "normal",
   "relevance": 4,
   "angle_strength": 3,
   "angle": "One sentence: the take worth sharing."}
]}
</result>
