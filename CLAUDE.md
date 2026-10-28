# LinkedIn AI Series Agent: Project Brief for Claude Code

Read this whole file before writing any code. It is the source of truth for this project.
If something here is unclear or conflicts with current API docs, check the official docs,
follow them, and note the change in README.md.

---

## 1. What we're building

A small Python agent, run by GitHub Actions, that automatically writes and publishes a
LinkedIn post **every 3 days** to my personal LinkedIn profile, with no manual step.

Each run:
1. Decides whether it's a posting day (3+ days since the last post).
2. Decides the post type (skill post or news post) and tone (straight or humorous) per the
   content mix in section 2a.
3. For a skill post: picks the next pending topic and uses the Claude API (with web search) to
   find 1–2 recent, credible developments on it. For a news post: scans the last 7 days of tech
   and AI news and picks one story worth reacting to.
4. Writes a post that sounds like a real person wrote it, not an AI.
5. Runs quality checks (automated lint + a second Claude "editor" pass). Rewrites if needed.
6. Publishes to LinkedIn through the **official LinkedIn Posts API** (never browser automation).
7. Records what was posted, commits the state back to the repo, and opens a GitHub Issue
   with the post text and link as a record/notification.

This is also a public portfolio project for Forward Deployed Engineer roles, so code quality,
README, and architecture docs matter.

---

## 2. The series

**Theme:** AI and the future of tech. Each post covers one skill and explains
(a) why it matters most in tech today and (b) how it helps enterprises be reliable.

**Seed topics (in order):**

| # | Topic | Angle |
|---|-------|-------|
| 1 | From Generative AI to Agentic AI | AI moving from producing content to completing work; actions raise the stakes; trust is the real skill. |
| 2 | Multi-agent systems | Small specialised agents + an orchestrator (Microsoft Agent Framework, Semantic Kernel, AutoGen, LangChain). Same lesson as microservices: contained failures, testable parts. |
| 3 | Model Context Protocol (MCP) | Open standard for exposing tools/data to agents. Build once, use with any agent. Fewer brittle one-off integrations, permissions in one place. |
| 4 | Copilot Studio and the Microsoft 365 Agent Toolkit | Low-code agents for business teams + pro-code inside Teams/Outlook. Adoption beats sophistication; governance avoids shadow AI. |
| 5 | The Forward Deployed Engineer | Engineers embedded with clients who learn the real process (exceptions, workarounds) and turn it into working systems. Domain knowledge is now core. |
| 6 | The backend underneath AI | Python, FastAPI/Flask microservices, event-driven design, containers (Azure Container Apps, App Service), retries, idempotency. Most of a production agent is normal engineering. |
| 7 | Agent evaluation and observability | Non-deterministic systems need systematic evals and trace-level visibility. Can't measure it → can't improve it; can't trace it → can't trust it. |
| 8 | Responsible AI and security | Least privilege, human-in-the-loop for high-impact actions, prompt-injection defence, audit logs. Trust is the bottleneck to adoption. |
| 9 | From prototype to production | Why most AI demos never reach production, and what it takes to harden one: reliability as an engineering discipline. |

**The series is open-ended. It has no fixed length and no wrap-up.** The 9 topics above are
only the starting backlog, not the whole series. The agent keeps posting every 3 days
indefinitely.

When fewer than 3 topics are pending, the agent asks Claude to propose 5 new topics within the
same theme (agentic AI, AI engineering, enterprise reliability, AI careers, integration/commerce
tech meeting AI, notable new developments in AI tooling), avoiding repeats or near-repeats of
anything already posted, and appends them as pending. New topics should mix evergreen skills
with timely ones drawn from the research step. Controlled by `auto_extend_topics` in config
(default `true`); it must never be possible for the backlog to run dry and silently stop posting.

**Never write a post that implies the series is ending or counts posts toward an end**:
no "final post", "wrapping up the series", "that's a wrap", "part 9 of 9", or recap posts
summarising "everything we've covered". Don't number posts.

---

## 2a. Content mix: news and humour

The feed should feel like a real person who follows the industry closely, not a course syllabus.

### Two post types
1. **Skill post**: the next topic from `topics.yaml` (as above), grounded in something recent.
2. **News post**: a reaction to one notable development in tech or AI from the **last 7 days**
   (model launches, new agent frameworks or protocols, big enterprise AI rollouts, outages or
   AI failures with lessons, funding/acquisitions that shift the market, regulation affecting
   how companies build with AI, developer tooling changes, commerce/retail tech meeting AI).
   The post must say what happened in one or two plain sentences, then give my take: what it
   means for engineers or enterprises, especially for reliability, and whether the hype matches
   reality.

**Mix:** about 1 in 3 posts is a news post (`news_post_ratio: 0.33`). Never two news posts in a
row. If a genuinely big story breaks (major model launch, major AI incident), a news post may
jump the queue, but still respect "not two in a row".

### Finding news (research step)
- Use web search to collect 5–10 candidate stories from the last 7 days, preferring original
  sources (company blogs, official announcements, reputable tech press) over aggregators.
- Score candidates on: relevance to the series theme, how much I have a real angle on it,
  freshness, and whether it's already been covered in the last 10 posts. Pick the best one.
- Verify the key facts against at least 2 sources, or 1 official source. If a story can't be
  verified, skip it. If no story qualifies, fall back to a skill post.
- Store the chosen story, its sources and the date in `history.json`. Never cover the same
  story twice.
- Never present rumours, leaks or speculation as fact. If it's early or unconfirmed, say so or skip it.
- No copying: summarise in my own words, never paste sentences from articles. Optionally
  include one source link at the end of the post if it adds value (max 1 link).

### Humour and sarcasm
About 1 in 3 posts (either type) should be noticeably funny or dry/sarcastic
(`humour_ratio: 0.33`). The rest can still have light wit, but stay mostly straight. Never two
heavily sarcastic posts in a row.

What good humour looks like here:
- Dry, observational, self-aware. The kind of joke engineers make in standups.
  E.g. poking at "we added AI to the slide deck, the process still runs on a spreadsheet",
  demos that work once, every product becoming "agentic" overnight, the gap between launch
  posts and production reality, integration work that's "just an API call".
- Sarcasm aimed at **hype, trends, buzzwords, and common industry habits**, including my own
  profession and myself.
- Funny, but with a real point underneath. Every humorous post still delivers a genuine insight
  about the skill or the news.
- Understated beats loud. One good line beats five jokes.

Hard limits:
- Never mock or insult named individuals, and never punch down at junior people, job seekers,
  non-technical colleagues, or users.
- Light ribbing of a company's *marketing claim or launch hype* is OK; no attacks on a company's
  people, no claims of wrongdoing, no defamation, nothing that could embarrass my employer or
  clients.
- No politics, religion, gender, nationality, caste or other identity-based jokes. No profanity.
- No sarcasm about layoffs, outages that hurt people, or anything tragic.
- Sarcasm must be obvious enough that a reader skimming fast won't take it literally. If a line
  could be read as a false factual claim, rewrite it.
- No meme formats, no "POV:", no "Me: ... Also me: ..." templates.

The editor pass must also score humorous posts on "is it actually funny and still professional"
(1–10, needs 7+) and cut jokes that fall flat rather than keep them.

---

## 3. About the author (the only personal facts the agent may use)

Store these in `content/author.md`. The agent may draw on them **only** when it fits naturally,
at most once per post, and must never invent anything beyond them.

- Ashish Ranjan, Functional Consultant at AbsoluteLabs (joined Nov 2024 as a Software Developer,
  moved to functional consulting after ~6 months). About 2 years of experience across
  engineering, consulting and ERPs.
- Works on retail commerce and order management: OMS platforms (KIBO, NewStore, Salesforce
  Commerce Cloud), marketplace integrations, ERP integrations via MuleSoft, product/stock/
  order/returns flows between systems.
- Delivered a marketplace integration that went live on schedule (offers and products synced
  to the marketplace; orders routed to downstream systems; returns and cancellations flowing back).
- Has done customer and order data migration work.
- Building toward a Forward Deployed Engineer career.

**Hard rules:**
- Never name clients or brands from work. Say "a retail brand", "a marketplace integration", etc.
- Never claim years of experience, titles, achievements, metrics, or tools not listed above.
- Never invent stories, conversations, numbers, or outcomes. If a post would benefit from a
  personal example and nothing above fits, write the post without one.

---

## 4. Writing style: the posts must NOT sound robotic or AI-generated

This is the most important quality requirement. Put the full guide in `content/style.md`
and include it in every generation and editing prompt.

### Voice
- First person, plain, conversational. Like a thoughtful engineer talking to peers over chai,
  not a brand account or a keynote.
- Have an actual opinion. It's fine to disagree with hype or say "I'm still figuring this out."
- Concrete over abstract: name a specific failure, system, or situation instead of
  "businesses everywhere".
- Simple words. If a 12-year-old wouldn't know the word and there's a plain one, use the plain one.
- Vary sentence length a lot. Some very short. Some longer ones that carry a real thought through.
- Natural, slightly imperfect rhythm is good. Don't polish every line into a slogan.
- Humour, when used, follows section 2a. Dry and understated, never forced.

### Structure
- 120–250 words.
- Rotate post formats so consecutive posts never share a shape. Track the format used in state
  and never repeat the previous 2. Formats:
  1. **Observation**: something I noticed at work or in the industry, and what it taught me.
  2. **Contrarian take**: a common belief, why it's incomplete.
  3. **Plain explainer**: explain the concept like I would to a colleague, with one real-world example.
  4. **Mistake / lesson**: a mistake people (or teams) commonly make and what to do instead.
  5. **Question-led**: open with a genuine question I'm thinking about, work through it.
  6. **Mini story**: a short scenario (clearly hypothetical if not from author facts).
  7. **News reaction**: what happened, in plain words, then my honest take on what it means.
  8. **Dry take**: a deadpan or sarcastic look at a trend or piece of news, with a real point underneath.
  Formats 7–8 are mainly for news posts and humorous posts, but can be combined with others.
- The first line must be a specific, concrete hook. No clickbait, no "Unpopular opinion:".
- Mostly short paragraphs. Bullet lists allowed in at most 1 of every 3 posts, max 4 bullets,
  no emoji bullets.
- End naturally. A question at the end in at most half of posts; never "What do you think? 👇".
- 0–1 emoji per post, and only if it would feel natural from a human.
- 3 hashtags max, on the last line.
- No headings, no bold text, no "Key takeaways".

### Banned (lint must fail the post if any appear, case-insensitive)
Words/phrases: delve, dive in, deep dive, let's dive, game-changer, game changer,
revolutionize, revolutionise, unlock, unleash, harness, leverage (as a verb), landscape,
realm, tapestry, paradigm, synergy, seamless, seamlessly, robust, cutting-edge,
in today's fast-paced, in today's world, in an era of, ever-evolving, ever-changing,
navigate the complexities, it's important to note, it's worth noting, at the end of the day,
here's the thing, here's why, buckle up, the future is here, let that sink in, elevate,
empower, transformative, supercharge, crucial role, pivotal, testament, fostering,
moreover, furthermore, in conclusion, to summarize, key takeaways, I'm excited to share,
thrilled to announce, agree?, thoughts?, unpopular opinion, final post, last post in this series,
wrapping up, that's a wrap, series recap, part X of Y (any "N of M" numbering).

Patterns (lint must fail):
- More than 1 em dash (—) in the whole post.
- "It's not X, it's Y" / "It's not just X, it's Y" / "This isn't X. It's Y." constructions.
- Three or more consecutive sentences starting with the same word.
- Any line that is only an emoji, or emoji used as bullet markers (✅ 🔍 ➡️ → etc.).
- More than 1 emoji total. More than 3 hashtags.
- Lists of exactly three adjectives or three parallel short phrases appearing more than once
  ("faster, cheaper, better").
- Opening line that is a rhetorical question starting "Ever wondered" / "Have you ever".
- Opening line identical in structure to any of the last 5 posts' opening lines.

### Two-pass generation
1. **Writer pass:** Claude writes the draft using: topic + angle, chosen format, style guide,
   author facts, 1–2 researched recent developments (with sources kept in state, not in the post
   unless a link genuinely adds value), and the last 5 posts (so it avoids repeating itself).
2. **Editor pass:** a separate Claude call acting as a blunt human editor. It scores the draft 1–10
   on "sounds like a real person" and lists every sentence that sounds like AI, corporate, or
   generic, then returns a rewritten version. Instructions: cut filler, replace abstractions with
   specifics, break the rhythm, remove anything that sounds like a slogan, keep all facts accurate.
3. **Lint:** deterministic Python checks (section above + length + hashtag/emoji counts).
4. If lint fails or editor score < 8, feed the failures back and retry. Max 3 attempts.
   If still failing: don't post; open a GitHub Issue labelled `needs-attention` with the best
   draft and the failure reasons.

### Facts
- Any claim about a recent development must come from the web search results in that run.
  If research turns up nothing solid, write the post without news references.
- No made-up statistics. No "studies show" without a real source.
- Don't overstate product capabilities; describe tools accurately.

---

## 5. Schedule

- GitHub Actions cron can't express "every 3 days" cleanly (`*/3` in the day field resets
  each month). So: **run daily at 03:30 UTC (9:00 AM IST)** and let the script decide.
- Post if `today - last_posted_date >= POST_INTERVAL_DAYS` (default 3).
- Also support `workflow_dispatch` with inputs: `dry_run` (bool), `force` (bool, ignore interval),
  `topic_id` (optional override).
- If a run is skipped (not a posting day) it exits cleanly with a log line, no commit.

---

## 6. Architecture & repo layout

```
linkedin-ai-series-agent/
├── CLAUDE.md                     # this file
├── README.md                     # setup guide + architecture diagram (Mermaid)
├── pyproject.toml                # or requirements.txt; Python 3.12
├── config.yaml                   # interval, model names, word limits, flags
├── content/
│   ├── topics.yaml               # backlog: id, title, angle, status, posted_on, linkedin_url
│   ├── style.md                  # full writing style guide (section 4)
│   └── author.md                 # allowed personal facts (section 3)
├── state/
│   └── history.json              # every post: date, type, topic_id/news story, format, tone, text, urn, sources
├── posts/                        # archive: YYYY-MM-DD-<slug>.md of each published post
├── src/agent/
│   ├── main.py                   # entrypoint / orchestration, CLI flags
│   ├── scheduler.py              # is-it-a-posting-day logic
│   ├── topics.py                 # pick next topic, auto-extend backlog
│   ├── research.py               # Claude + web search for recent developments
│   ├── news.py                   # find, score, verify and pick recent news stories
│   ├── mix.py                    # decide post type (skill/news) and tone (straight/humorous)
│   ├── writer.py                 # writer pass
│   ├── editor.py                 # editor pass
│   ├── lint.py                   # deterministic anti-AI checks
│   ├── linkedin.py               # LinkedIn Posts API client
│   ├── notify.py                 # GitHub Issue creation
│   ├── state.py                  # read/write history + topics
│   └── prompts/                  # prompt templates as .md files, not inline strings
├── scripts/
│   └── linkedin_auth.py          # one-time local OAuth helper to get token + person URN
├── tests/                        # pytest; mock all external APIs
└── .github/workflows/
    ├── post.yml                  # daily scheduled run
    └── tests.yml                 # run tests + lint on push/PR
```

---

## 7. Claude API

- Use the official `anthropic` Python SDK.
- Model names in `config.yaml`, not hardcoded. Default writer/editor: `claude-sonnet-5-5`.
  Check the current models list in the docs (https://docs.claude.com) and use a current one.
- Research step: use Anthropic's server-side web search tool. Check the docs for the current
  tool type/version string rather than assuming it.
- Ask for structured JSON output where useful (research results, editor scores) and parse safely.
- Log token usage per run. Keep costs low: one post every 3 days should cost cents per month.
- `ANTHROPIC_API_KEY` from env/GitHub Secrets. Note in README: API usage is billed separately
  from a Claude.ai subscription and needs prepaid credit at console.anthropic.com.

---

## 8. LinkedIn API

- Official API only. Never scrape or automate the LinkedIn website.
- Developer app at https://developer.linkedin.com with products:
  "Sign In with LinkedIn using OpenID Connect" and "Share on LinkedIn".
  Scopes: `openid profile w_member_social`.
- `scripts/linkedin_auth.py`: runs locally, opens the browser for 3-legged OAuth, catches the
  redirect on `http://localhost:8000/callback`, exchanges the code for an access token, calls
  the userinfo endpoint to get `sub`, and prints:
  - `LINKEDIN_ACCESS_TOKEN`
  - `LINKEDIN_PERSON_URN` (= `urn:li:person:<sub>`)
  - the token expiry date
  with instructions for adding them to GitHub Secrets/Variables.
- Publishing: `POST https://api.linkedin.com/rest/posts` with headers
  `Authorization: Bearer ...`, `LinkedIn-Version: <YYYYMM>` (configurable; verify a currently
  supported version in the docs), `X-Restli-Protocol-Version: 2.0.0`. Body: author = person URN,
  commentary = post text, visibility PUBLIC, distribution to the main feed, lifecycleState
  PUBLISHED, not reshare-disabled. Read the post ID/URN from the response header.
- **Escape reserved characters** in `commentary` per LinkedIn's "little text format" rules
  (parentheses, brackets, braces, angle brackets, @, #-handling, etc.), otherwise text gets
  truncated. Hashtags must still render as hashtags. Write tests for this.
- Retries with backoff on 429/5xx. On 401: open a `needs-attention` issue saying the token
  expired and how to refresh it.
- **Token expiry:** member tokens typically last ~60 days and usually can't be refreshed
  automatically. Store `LINKEDIN_TOKEN_EXPIRES_ON` as a GitHub Variable. Every run, if expiry is
  within 7 days, open (or update, don't duplicate) an issue reminding me to re-run
  `scripts/linkedin_auth.py`.
- Text-only posts for v1. Design `linkedin.py` so image support can be added later.

---

## 9. Notifications & state

- After publishing, open a GitHub Issue titled `Posted: <topic title> (<date>)` containing the
  post text, LinkedIn URL, post type, tone, format used, editor scores, and research sources.
  Label `posted`.
  (GitHub emails me about new issues, so this doubles as notification.)
- Optional review window, off by default: `review_window_hours` in config. If > 0, the run
  creates the draft + an issue labelled `scheduled`, and a later run publishes it once the
  window has passed unless the issue was closed or labelled `reject`. Default `0` = publish
  immediately (fully automatic, which is what I want).
- The workflow commits `state/`, `content/topics.yaml` and `posts/` back to the repo
  (`permissions: contents: write, issues: write`) with message `chore: post <slug>`.
- Use a `concurrency` group so two runs never overlap.

---

## 10. Secrets & config

GitHub Secrets: `ANTHROPIC_API_KEY`, `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_PERSON_URN`.
GitHub Variables: `LINKEDIN_TOKEN_EXPIRES_ON`.
Local dev: `.env` (in `.gitignore`), loaded with python-dotenv. Provide `.env.example`.
Never log tokens or full API responses containing them.

`config.yaml` defaults:
```yaml
post_interval_days: 3
post_time_utc: "03:30"
writer_model: claude-sonnet-5-5
editor_model: claude-sonnet-5-5
min_words: 120
max_words: 250
max_hashtags: 3
max_emojis: 1
max_em_dashes: 1
min_editor_score: 8
max_attempts: 3
news_post_ratio: 0.33
news_lookback_days: 7
humour_ratio: 0.33
min_humour_score: 7
auto_extend_topics: true
review_window_hours: 0
linkedin_api_version: "YYYYMM"   # set to a currently supported version
dry_run: false
```

---

## 11. Quality bar for the code

- Python 3.12, type hints, small modules, clear names, docstrings on public functions.
- `ruff` for lint/format, `pytest` for tests. Mock Anthropic and LinkedIn in all tests.
- Tests must cover: posting-day logic (incl. month boundaries), topic selection and
  auto-extend, format rotation, every lint rule (pass and fail cases), LinkedIn text escaping,
  retry/failure paths, token-expiry warning, dry-run mode (no network writes), content mix
  rules (ratios, never two news posts or two sarcastic posts in a row), news de-duplication,
  fallback to a skill post when no news qualifies.
- `--dry-run` prints the final post and lint/editor results without posting or committing.
- Structured logging; a clear one-line summary at the end of each run.
- README: what it does, Mermaid architecture diagram, setup steps (LinkedIn app, OAuth helper,
  secrets, Anthropic credit, first dry run), how to add topics, how to change tone, costs,
  and limitations (60-day token, text-only v1). Written for a recruiter skimming it too.

---

## 12. Build order

1. Scaffold repo, config, content files (topics, style, author) and state.
2. `lint.py` + tests (the anti-AI checks are the heart of quality).
3. `topics.py`, `scheduler.py`, `state.py` + tests.
4. `research.py`, `writer.py`, `editor.py` with prompts in `prompts/`.
5. `news.py`, `mix.py`, then `main.py` with `--dry-run`; run it locally and show me 4 sample
   posts before going further: a straight skill post, a humorous skill post, a straight news
   post and a sarcastic news post.
6. `scripts/linkedin_auth.py`, then `linkedin.py` + tests.
7. `notify.py`, GitHub workflows, README.
8. Final checklist for me to go live.

Ask me before changing anything in sections 2a, 3 or 4.
