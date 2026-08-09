from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..settings import settings

logger = logging.getLogger(__name__)


async def ingest_and_match() -> None:
    from ..pipeline.ingest import run_ingest
    from ..pipeline.match import run_match

    try:
        await run_ingest()
        await run_match()
    except Exception:
        logger.exception("scheduled ingest+match failed")


async def discover() -> None:
    from ..agents.discovery import run_discovery

    try:
        await run_discovery()
    except Exception:
        logger.exception("scheduled discovery failed")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        ingest_and_match,
        "interval",
        hours=settings.ingest_interval_hours,
        id="ingest_and_match",
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        discover,
        "interval",
        days=settings.discovery_interval_days,
        id="discover",
        coalesce=True,
        max_instances=1,
    )
    return scheduler
