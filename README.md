# recruiting-agent

Long-running personal recruiting agent. Each clone or deployment belongs to one
candidate and develops a private, calibrated search harness over time.

## How it works

```
ingest ──► prefilter ──► score ──► dashboard
 ATS APIs   Haiku, lenient  Sonnet, full   review & apply
 (Greenhouse/ title-level    description,   manually
 Lever/Ashby) gate           0–100 + why
```

- **Ingest** — every tracked company's public ATS API (Greenhouse / Lever / Ashby).
  `ra probe` auto-detects each company's board. Postings are deduped by
  `(company, external_id)`; edits are detected via content hash.
- **Prefilter** — a cheap, lenient Haiku pass over title/department/location in
  batches of 25. Judges substance, not keywords: "Chief of Staff" or "Strategic
  Projects" pass through; IC engineering/research roles are rejected.
- **Score** — Sonnet reads the full description against `profile/resume.md` +
  `profile/profile.yaml` and returns a 0–100 score, apply/maybe/skip
  recommendation, reasoning, and red flags. Results are cached per
  (job content, profile version) — editing your profile re-scores everything.
- **Discover** — weekly WebSearch agent proposes companies allowed by the candidate thesis; they
  land as *pending approval* on the dashboard, never auto-tracked.
- **Dashboard** — review matches (save / applied / dismiss), browse jobs,
  manage companies, watch run history. Scheduler runs ingest+match every 2h.

## Setup

```bash
uv sync
cp .env.example .env   # optional: Langfuse keys for LLM tracing
uv run ra seed         # load config/companies.yaml into the DB
uv run ra probe        # resolve each company's ATS board
```

LLM calls go through the Claude Agent SDK and use your authenticated `claude` CLI
(or `ANTHROPIC_API_KEY` if set).

## Usage

```bash
uv run ra ingest         # fetch open roles from all boards
uv run ra match          # prefilter + score anything new
uv run ra discover       # scout new frontier companies
uv run ra serve          # dashboard at http://127.0.0.1:8000 (+ scheduler)
uv run pytest            # connector & dedupe tests
```

On first launch, the dashboard asks for a resume, conducts a brief adaptive
interview, calibrates company and role judgment, and writes a private gitignored
`workspace/`. The uploaded resume remains intact; the interview agent inspects it
and asks only decision-relevant follow-ups.

Run the dashboard and scheduler separately:

```bash
uv run ra serve    # web UI only
uv run ra worker   # restartable coordinator wakes
```

Every cycle reloads the approved search constitution, calibration anchors, event
history, and checkpoint. It never depends on one indefinitely running model
conversation. See `docs/modal.md` for the optional single-candidate Modal scaffold.
Companies live in `config/companies.yaml`; `ra probe` resolves new entries.
