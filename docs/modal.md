# Modal deployment scaffold

Modal is optional. Local development continues to use SQLite, a local `workspace/`,
and the separate `ra worker` process.

## Requirements

1. Provision PostgreSQL and put `DATABASE_URL` in a Modal Secret named
   `recruiting-agent-secrets`, together with the Anthropic/Langfuse keys you use.
2. Create a Modal proxy token for the deployed web endpoint. This is a bespoke
   single-candidate deployment; there is no application-level identity system.
3. Install the optional dependencies with `uv sync --extra modal`.
4. Deploy from the repository root:

   ```bash
   modal deploy src/recruiting_agent/modal_app.py
   ```

The ASGI endpoint and scheduled coordinator share a persistent candidate Volume.
PostgreSQL is authoritative for cycles, events, actions, leases, jobs, and feedback.
The Volume stores only the candidate's human-readable harness and is configured with
one web container and one leased coordinator. Do not point `DATABASE_URL` at SQLite
on a Modal Volume.

Long tasks run outside HTTP requests. The Cron function wakes every 30 minutes; the
coordinator's persisted cadence decides which work is actually due, and idempotency
keys make duplicate wakes safe.
