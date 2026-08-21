from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..settings import settings

logger = logging.getLogger(__name__)


async def coordinator_tick() -> None:
    from ..coordinator import SearchCoordinator

    try:
        await SearchCoordinator().run_cycle("scheduled")
    except Exception:
        logger.exception("scheduled coordinator cycle failed")


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        coordinator_tick,
        "interval",
        minutes=30,
        id="coordinator_tick",
        coalesce=True,
        max_instances=1,
    )
    return scheduler
