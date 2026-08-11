# recruiting-agent

A long-running personal recruiting agent. Each clone or deployment belongs to one
candidate and develops a private, calibrated search harness over time.

## How it works

On first launch, the dashboard asks for a resume, conducts a brief adaptive
interview, calibrates company and role judgment, and writes a private gitignored
`workspace/`. The uploaded resume remains intact; the interview agent inspects the
file and asks decision-relevant follow-ups.

The active search then runs as bounded, restartable cycles:

```text
wake → ingest → prefilter → score → consolidate memory → digest → checkpoint
```

- Greenhouse, Lever, and Ashby connectors ingest authoritative job boards.
- Candidate-specific behavior comes from the workspace search constitution,
  decision rubric, company thesis, calibration anchors, and approved memory.
- Job content and complete harness hashes prevent unnecessary re-evaluation.
- An immutable event history and idempotent actions make cycles safe to retry.
- Explicit corrections can propose memory changes, but policy changes require
  candidate approval.
- Company discovery follows `strict` or `exploratory` scope and never activates a
  suggestion automatically.

## Setup

```bash
uv sync
cp .env.example .env       # optional API/tracing configuration
uv run ra serve            # http://127.0.0.1:8000
```

Complete onboarding in the browser, then resolve and ingest the approved company
sources:

```bash
uv run ra probe
uv run ra run-cycle
uv run ra worker           # persistent local scheduler, separate from the web app
```

For an existing checkout, `uv run ra import-legacy` copies the old tracked profile
and company list into the private workspace before onboarding continues.

## Useful commands

```bash
uv run ra ingest
uv run ra match
uv run ra discover
uv run ra run-cycle --trigger manual
uv run pytest
```

The dashboard exposes Today, Matches, Companies, Search State, Agent Activity,
Memory Revisions, Feedback, and Source Health. It remains a manual decision tool;
the agent does not apply to jobs or contact anyone.

Every scheduled task reconstructs coherence from the approved harness, event
history, and checkpoint. It does not depend on one indefinitely running model
conversation. See [`docs/modal.md`](docs/modal.md) for the optional bespoke Modal
deployment scaffold.
