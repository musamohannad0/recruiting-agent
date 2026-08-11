# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                       # install
uv run ra seed                # create DB + load config/companies.yaml
uv run ra probe               # resolve each company's ATS board token (add --force to re-probe)
uv run ra ingest              # fetch postings from every active company's board (--company "Anthropic")
uv run ra match               # prefilter + score anything new (--limit N, --rescore)
uv run ra discover            # WebSearch agent proposes new companies as pending_approval
uv run ra serve               # dashboard on 127.0.0.1:8000 AND the background scheduler
uv run pytest                 # all tests
uv run pytest tests/test_connectors.py::test_greenhouse_fetch    # single test
```

No linter or formatter is configured. `pytest` runs with `asyncio_mode = "auto"`, so `async def test_*` needs no decorator. Connector tests use `respx` to mock ATS HTTP; nothing in the suite hits the network or an LLM.

LLM calls go through the Claude Agent SDK, which shells out to the authenticated `claude` CLI. Everything in `.env` is optional: `ANTHROPIC_API_KEY` only if the CLI isn't already authenticated, and the Langfuse keys only if you want tracing (it is silently skipped when they're unset).

## Architecture

Four stages, each independently runnable from the CLI and chained by the scheduler:

```
ingest ──► prefilter ──► score ──► dashboard
ATS APIs   Haiku,        Sonnet,   human triage
           batches of 25 full JD   (save/applied/dismissed)
```

**The two-hash cache contract is the core design idea** and spans several files. Nothing is re-evaluated unless one of two hashes changes:

- `JobPosting.content_hash` (`connectors/base.py`) = sha256 of title + location + description. Ingest uses it to tell "edited" from "unchanged".
- `Profile.hash` (`agents/profile.py`) = sha256 of `profile/resume.md` + `profile/profile.yaml`.

`Prefilter` and `Match` rows both store `(job_id, content_hash, profile_hash)`, and `pipeline/match.py` builds skip-sets from them. **Consequence: editing anything in `profile/` changes the profile hash and re-prefilters + re-scores every active job on the next `ra match`.** That is the main cost lever — check how many active jobs exist before touching those files.

Other structural facts worth knowing:

- **`agents/llm.py::llm_json` is the single LLM entry point** — one-shot, `allowed_tools=[]`, JSON-schema structured output, `max_turns=3` (structured output consumes an internal turn). It wraps the call in a Langfuse generation when keys are set, and returns `LLMResult(data, trace_id, cost_usd)`. Add new model calls here rather than calling `query()` directly. `agents/discovery.py` is the one deliberate exception — it needs `WebSearch`/`WebFetch` tools and a long `max_turns`.
- **Connectors implement a Protocol** (`connectors/base.py`): `fetch(board_token)` and `board_exists(token)`. `registry.py` maps `AtsType` → instance. `probe.py` auto-discovers board tokens by trying slug variants × all three ATSes; `config/companies.yaml` carries `slugs:` hints for companies whose token isn't derivable from the name (several entries there document *why* a naive slug resolves to the wrong company — preserve those comments).
- **Ingest never deletes.** `pipeline/ingest.py::upsert_jobs` dedupes on `(company_id, external_id)`, updates on content-hash change, and flips vanished postings to `is_active=False` (reactivating them if they come back).
- **Every pipeline run is recorded.** `pipeline/runs.py::track_run` is a context manager that writes a `Run` row with `stats_json`; the caller sets `handle.stats` before exiting. Wrap new pipeline entry points in it so they appear on `/runs`.
- **The scheduler lives inside the FastAPI lifespan** (`web/app.py`), so ingest+match (every 2h) and discover (weekly) only run while `ra serve` is up. There is no separate daemon.
- **Discovered companies are never auto-tracked** — they land as `pending_approval` and need a click on `/companies`.

## Where behavior is tuned

Matching quality is controlled by prose, not code paths:

- `profile/profile.yaml` + `profile/resume.md` — the candidate's targets, hard filters, and location constraints.
- `SYSTEM_PROMPT` in `agents/prefilter.py` (lenient gate — bias toward `plausible`) and `agents/scoring.py` (0–100 rubric with explicit score bands).
- `PROMPT_VERSION` in `agents/scoring.py` — stamped onto `Match` rows for provenance. Bump it when the rubric changes.

The prefilter is intentionally fail-open: if the model omits an index, that job defaults to `plausible`. A failed prefilter batch writes no rows, so it is simply retried on the next run.

## Gotchas

- `SQLModel.metadata.create_all` is the only schema mechanism — **there are no migrations**. Adding a column to `models.py` will not alter an existing `data/app.db`; you must `ALTER TABLE` by hand or delete the DB.
- `Prefilter` and `Match` rows accumulate on every re-score and are never pruned.
- `LLMResult.cost_usd` is returned but never persisted, so there is no spend history.
- Greenhouse `posted_at` is populated from the API's `updated_at`, so it shifts whenever a JD is edited (the API also offers `first_published`).

## Known issues (verified by reproduction, not yet fixed)

Both are live consequences of `Match` rows being per-scoring-run rather than per-job:

1. **`/matches` shows stale scores.** `web/app.py::_match_rows` orders by `score DESC` then dedupes by job keeping the first row, so it displays each job's *highest-ever* score while `/jobs/{id}` shows the *latest*. After a rubric change that lowers scores, the list keeps showing the old higher number and the two views disagree.
2. **Triage state is lost on re-score.** `user_status` lives on `Match`, so a profile edit creates a fresh row with `user_status=None` — dismissed jobs reappear in the `new` queue and vanish from the `dismissed` filter.
