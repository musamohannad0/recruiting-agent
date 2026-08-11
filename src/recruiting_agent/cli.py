from __future__ import annotations

import asyncio

import typer

app = typer.Typer(help="Recruiting agent CLI", no_args_is_help=True)


@app.command()
def seed() -> None:
    """Initialize the DB and load companies from config/companies.yaml."""
    from .db import get_session, init_db
    from .seed import seed_companies

    init_db()
    with get_session() as session:
        added, existing = seed_companies(session)
    typer.echo(f"Seeded companies: {added} added, {existing} already present.")


@app.command()
def probe(
    name: str = typer.Argument(None, help="Company name (default: all unresolved)"),
    force: bool = typer.Option(False, help="Re-probe companies that already resolved"),
) -> None:
    """Detect each company's ATS (greenhouse/lever/ashby) and board token."""
    from .connectors.probe import probe_companies

    asyncio.run(probe_companies(name=name, force=force))


@app.command()
def ingest(
    company: str = typer.Option(None, help="Only this company"),
) -> None:
    """Fetch job postings from all active companies' ATS APIs."""
    from .pipeline.ingest import run_ingest

    asyncio.run(run_ingest(company_name=company))


@app.command()
def match(
    limit: int = typer.Option(None, help="Max jobs to evaluate this run"),
    rescore: bool = typer.Option(False, help="Ignore cached verdicts and re-evaluate"),
) -> None:
    """Prefilter (cheap model) then score (smart model) unevaluated active jobs."""
    from .pipeline.match import run_match

    asyncio.run(run_match(limit=limit, rescore=rescore))


@app.command()
def discover() -> None:
    """Search for companies allowed by the candidate thesis; adds pending suggestions."""
    from .agents.discovery import run_discovery

    asyncio.run(run_discovery())


@app.command("run-cycle")
def run_cycle(trigger: str = typer.Option("manual", help="Reason for waking the coordinator")) -> None:
    """Run one bounded, restartable long-running-agent cycle."""
    from .coordinator import SearchCoordinator

    result = asyncio.run(SearchCoordinator().run_cycle(trigger))
    typer.echo(result)


@app.command()
def worker() -> None:
    """Run the local scheduler separately from the dashboard web process."""
    from .pipeline.scheduler import build_scheduler

    async def serve_scheduler() -> None:
        scheduler = build_scheduler()
        scheduler.start()
        typer.echo("Coordinator worker started. Press Ctrl-C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            scheduler.shutdown(wait=False)

    try:
        asyncio.run(serve_scheduler())
    except KeyboardInterrupt:
        pass


@app.command("import-legacy")
def import_legacy() -> None:
    """Copy the tracked legacy profile and company list into the private workspace."""
    from .workspace import workspace

    workspace.import_legacy()
    typer.echo(f"Legacy inputs copied to {workspace.root}; continue onboarding in the dashboard.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
) -> None:
    """Run the dashboard web server. Use `ra worker` for scheduled cycles."""
    import uvicorn

    uvicorn.run("recruiting_agent.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
