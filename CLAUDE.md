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

`SearchCoordinator` is deterministic and restartable. It acquires a database lease,
executes idempotent actions, records immutable events, consolidates operational
memory, checkpoints, and exits. Local scheduling runs through `ra worker`, never the
FastAPI lifespan.

The two-hash cache remains central:

- job `content_hash` changes when posting substance changes;
- profile hash is now the complete approved candidate harness hash.

`JobReview` owns save/dismiss/apply state independently of historical `Match` rows.
Match lists select the newest score for the active harness; do not regress to
score-first deduplication.

## Persistence

`db.init_db` runs `SQLModel.metadata.create_all` followed by versioned migrations.
Never require users to delete their existing database for a schema change.

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
