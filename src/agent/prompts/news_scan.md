Today is $today. Below are headlines from the last $lookback_days days, each with an id like [A1],
where it was published, the date, and a short summary when the feed had one.

Pick 5 to 10 candidate stories that $author_first_name, who writes about $series_writes_about,
might react to on LinkedIn: $news_focus.

Rules:
- Use only what the headlines and summaries say. Nothing from memory.
- Several headlines may cover the same story: list all their ids together in headline_ids.
  A story needs either 2 headlines from different sites, or 1 marked "official".
- Mark rumours, leaks, "reportedly" stories and speculation as "confirmed": false.
- Skip stories already covered in earlier posts:
$covered

For each candidate, rate:
- relevance (1-5): fit with $series_topic_areas.
- angle_strength (1-5): how much $author_first_name has a real, specific take on it ($news_take),
  beyond restating the news.
- significance: "major" only for a genuinely big story ($news_major). Otherwise "normal".

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
