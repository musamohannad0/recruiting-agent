# recruiting-agent

A self-hosted, long-running recruiting agent for one candidate. Each clone or
deployment owns one private search workspace—there is no OIDC or multi-user account
layer.

The agent continuously collects roles, evaluates them against a candidate-specific
harness, records what it did, and asks for approval before changing durable policy or
expanding the company universe. Applications remain manual.

## Candidate onboarding

First launch redirects to a resumable onboarding flow:

1. Upload the resume you already use (`.pdf`, `.txt`, or `.md`). The file is preserved
   for the agent to inspect; the application does not attempt to normalize it into a
   brittle resume schema.
2. Describe target work, working style, locations, company stage, verticals, hard
   exclusions, and review cadence.
3. Answer only the follow-up questions that could materially change the search.
4. Start from a ranked 40-company set, then remove misses, mark priorities, and add
   companies the agent missed.
5. Calibrate role judgment on contrasting edge cases.
6. Review the resulting policy and activate the first search.

Onboarding writes a gitignored `workspace/` containing the candidate context, search
constitution, decision rubric, company thesis, calibration anchors, cadence, approved
memory, and a versioned manifest. The complete workspace becomes the harness hash used
to select current scores and invalidate stale evaluations.

## Watching the agent work

`/activity` is the operations view: the current cycle's phase track, every recorded
action with what it produced, and what the cycle cost — dollars, model calls, and the
share of prompt tokens served from cache. It updates in place while a cycle runs, so
scroll position and open detail panels survive a refresh.

A cycle that loses one stage still finishes the rest and reports `partial`, naming what
failed. `/runs` keeps the per-pipeline history; `/memory` holds revisions waiting for
your approval; `/sources` shows which career systems are still answering.

## Cost

Nothing is re-evaluated unless the job content or the approved harness changes. Within
a run, the candidate harness is sent once as a cached prefix rather than once per role,
and a text resume rides along in that prefix instead of being fetched per role. Spend is
recorded per action and per cycle, and the last cycle's cache hit rate is on `/activity`
— if it reads low, something is varying inside the prefix.

Tune models, retry attempts, per-call spend ceilings, and concurrency in `.env`
(see `src/recruiting_agent/settings.py`).

## Local setup

Requirements: Python 3.12, [`uv`](https://docs.astral.sh/uv/), and either an authenticated
Claude CLI or `ANTHROPIC_API_KEY`.

```bash
uv sync
cp .env.example .env       # optional API and Langfuse configuration
uv run ra serve            # dashboard at http://127.0.0.1:8000
```

Complete onboarding in the dashboard. Activation starts the first bounded search cycle
immediately. For ongoing local operation, keep the scheduler in a separate terminal:

```bash
uv run ra worker
```

The web process never owns the scheduler, so restarting `ra serve` does not start a
search or create competing cycles. Run exactly one worker per deployment. The worker's
first check occurs about 30 minutes after it starts; use the command below when an
immediate manual wake is desired:

```bash
uv run ra run-cycle --trigger manual
```

For an older checkout, `uv run ra import-legacy` copies the tracked profile and company
configuration into the private workspace before onboarding continues.

## Search cadence

The worker wakes every 30 minutes. Idempotency keys and a renewable persisted lease
prevent duplicate work and overlapping long cycles. The candidate's
`workspace/policy/cadence.yaml` controls when each action is actually due:

| Work | Local default |
| --- | ---: |
| Supported ATS ingestion and role matching | Every 2 hours |
| Source verification and unsupported-site scouting | Daily |
| Exploratory company discovery | Weekly |
| Memory consolidation | Weekly |
| Recalibration proposal | After 5 explicit corrections |

Greenhouse, Lever, and Ashby are authoritative connectors. Unsupported career sites are
scouted separately. Exploratory company suggestions remain pending until approved.

## How a cycle stays coherent

```text
wake → acquire/renew lease → load checkpoint → run due idempotent actions
     → validate events → consolidate memory → prepare briefing → checkpoint
```

- General-purpose agents receive task-specific tools and skills plus the active candidate
  harness; system prompts remain candidate-neutral.
- Job content and complete harness hashes prevent unnecessary re-evaluation.
- Actions, events, feedback, reviews, source health, and memory revisions are persisted in
  version-migrated SQLite locally.
- Candidate memory and policy live as inspectable Markdown/YAML files in `workspace/`.
- Policy-changing memory revisions require approval; the append-only search journal can
  be consolidated automatically. Hard constraints are not silently rewritten.
- A job review survives rescoring, while the dashboard shows only the latest score for the
  active harness.

## Dashboard and evaluation evidence

The dashboard includes Today, Matches, Companies, Agent Activity, Search Policy, Memory
Revisions, Feedback, Source Health, all jobs, and run history.

Matches are ordered by score descending. Open **Why this score** to inspect the stored
rationale, seniority/location evidence, watch-outs, model, harness, prompt version,
timestamp, cost, and trace availability. The job page exposes the full evaluation record
and a correction form. Optional Langfuse tracing is enabled only when its keys are present
in `.env`.

## Useful commands

```bash
uv run ra probe             # identify supported ATS sources
uv run ra ingest            # fetch openings from active sources
uv run ra match             # evaluate unevaluated roles for the active harness
uv run ra discover          # propose adjacent companies
uv run ra run-cycle         # one bounded coordinator wake
uv run pytest               # full test suite
```

Local state defaults to `data/app.db`; candidate files default to `workspace/`. Both are
private deployment state and should be backed up together.

## Modal-ready deployment

[`docs/modal.md`](docs/modal.md) describes the optional Modal scaffold: an ASGI endpoint,
bounded Cron coordinator ticks, PostgreSQL for concurrent state, a single-writer Modal
Volume for the candidate workspace, secrets, and proxy-token protection. Modal support is
scaffolding only and is not deployed automatically.
