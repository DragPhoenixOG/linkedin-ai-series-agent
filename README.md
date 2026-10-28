# LinkedIn AI Series Agent

A small Python agent, run by GitHub Actions, that researches, writes, quality-checks and publishes
a LinkedIn post every 3 days on AI and the future of tech.

> Work in progress. The full setup guide, architecture diagram and go-live checklist arrive in
> build step 7. See `CLAUDE.md` for the project brief.

## Try it locally (dry run)

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env            # add ANTHROPIC_API_KEY
.venv/bin/python -m agent.main --dry-run --force
```

`--dry-run` prints the final post plus the lint and editor results. It never posts, commits, or
changes files in `state/`, `content/` or `posts/`.

## Notes on API choices

- Web search uses `web_search_20260318`, the current version in the Anthropic web search docs
  (it adds `response_inclusion` on top of the dynamic filtering in `web_search_20260209`).
- Research and news calls ask for JSON inside `<result>` tags instead of using structured
  outputs: web search always returns citations, and the docs list citations as incompatible
  with `output_config.format`. Calls without web search (editor, topic proposals) use
  structured outputs.
