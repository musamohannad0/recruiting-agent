# CLAUDE.md

## Commands

```bash
uv sync
uv run ra serve             # dashboard only
uv run ra worker            # local scheduler process
uv run ra run-cycle         # one restartable coordinator cycle
uv run ra probe
uv run ra ingest
uv run ra match
uv run ra discover
uv run pytest
```

Tests mock ATS HTTP and agent responses; the suite must not call the network or an
LLM. Modal dependencies are optional through `uv sync --extra modal`.

## Architecture

This is a bespoke single-candidate application, not a multi-tenant service.
Candidate files live under the gitignored `workspace/`; tracked `profile/` files are
synthetic compatibility examples.

`workspace/manifest.yaml` identifies the active harness version. Candidate-specific
judgment comes from context, policy, calibration anchors, memory, and the generated
candidate skill. Python system prompts must remain candidate-neutral.

`AgentRuntime` is the LLM entry point. A `TaskSpec` selects only the context, skills,
tools, model, schema, and budget needed for a task. Scoped custom tools may record
events or propose revisions, but agents must never write policy or durable memory
directly.

**Everything invariant across a batch belongs in the system prompt.** `AgentRuntime`
composes task instructions + harness context into one prefix and passes only the
per-item payload as the user turn. An identical prefix is what makes prompt caching
pay off across a scoring run — a run of N roles sends the harness once, not N times.
Never move harness content back into the per-item prompt. `LLMResult.cache_read_tokens`
is how you check it is working; the Activity page reports it as "% cached".

Harness context is memoised per `(workspace, harness_hash, sections)` and the manifest
per `(mtime_ns, size)`, so scoring N roles does not re-read the same files N times.
`default_runtime()` returns a shared instance so those caches survive across calls —
constructing a fresh runtime per call defeats them.

`llm_json` retries only transient failures (overload, rate limit, gateway, missing
structured output) and never retries a 400. `effort` is dropped for models that reject
it — Haiku 4.5 and Sonnet 4.5 do. `track_usage()` accumulates tokens and cost across
concurrent calls; the coordinator wraps every action in one and persists the totals.

`SearchCoordinator` is deterministic and restartable. It acquires a database lease,
executes idempotent actions, records immutable events, consolidates operational
memory, checkpoints, and exits. Local scheduling runs through `ra worker`, never the
FastAPI lifespan.

**A failed action does not end the cycle.** `_execute_action` returns a failure rather
than raising, the remaining stages still run, and the cycle finishes `partial` with a
summary naming what broke. A flaky career-site probe must never cost the operator that
cycle's matching. Only a genuine coordinator fault (lost lease, database error) ends a
cycle as `failed`.

Per-action and per-cycle spend (`cost_usd`, `llm_calls`, `input_tokens`,
`output_tokens`, `cached_tokens`) is persisted, so a run's cost is inspectable after
the terminal has scrolled.

A **cached** action keeps the `cycle_id` of the run that did the work, so the action
rows alone make the current cycle look as though it skipped that stage. The phase track
reads the cycle's own `stats_json` instead — do not regress to keying it on actions.

The two-hash cache remains central:

- job `content_hash` changes when posting substance changes;
- profile hash is now the complete approved candidate harness hash.

`JobReview` owns save/dismiss/apply state independently of historical `Match` rows;
`Match.user_status` is retired. Match lists select the newest score for the active
harness **in SQL** (a `row_number()` window over `job_id`) — do not regress to loading
every match row and de-duplicating in Python, and do not assign display-only state onto
live ORM instances.

Nothing reaches the dashboard as raw JSON. `reporting.py` is the shared vocabulary:
`payload_facts` turns a stored payload into labelled facts and `summarize_action` turns
it into a sentence. It lives at the package root because the coordinator uses it too —
the candidate's search journal is prose written with the same helpers, because an agent
reads that file back as context and a JSON dump costs tokens to say less.

Markdown is rendered through the `| markdown` Jinja filter, configured with
`html=False`: job descriptions originate in third-party ATS HTML, so embedded markup is
escaped rather than passed through. Do not swap in a renderer that emits source HTML.

## Persistence

`db.init_db` runs `SQLModel.metadata.create_all` followed by versioned migrations.
Never require users to delete their existing database for a schema change.

**Migrations must not read through the current ORM models.** A migration upgrades a
schema the code no longer describes; migration 1 uses raw SQL against `matches` for
exactly this reason. Every migration is idempotent and must be a no-op on a fresh
database.

Local deployments use SQLite and filesystem workspace storage. The optional Modal
scaffold uses PostgreSQL for operational state and a Volume for human-readable
candidate files. Volume writes must remain single-writer.

Ingestion never deletes rows. Authoritative ATS disappearance marks a job inactive;
agentic sources need repeated direct verification before deactivation.

## Behavior and safety

Confirmed policy outranks calibration, explicit feedback, implicit review actions,
and agent inference—in that order. Contradictions create clarification or revision
proposals instead of silent policy changes.

Discovered companies are pending until approved. The application finds and ranks
jobs only; applying and outbound contact are outside scope.
