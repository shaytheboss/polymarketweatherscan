"""
APScheduler entry point.  Runs as part of the FastAPI lifespan (app/main.py).
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.workers.jobs import job_refresh_markets, job_poll_prices

logger = logging.getLogger(__name__)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        job_refresh_markets,
        IntervalTrigger(seconds=settings.market_refresh_interval),
        id="refresh_markets",
        name="Refresh Polymarket tennis markets",
        max_instances=1,
        misfire_grace_time=60,
        next_run_time=None,  # run immediately on start (see main.py)
    )
    scheduler.add_job(
        job_poll_prices,
        IntervalTrigger(seconds=settings.price_poll_interval),
        id="poll_prices",
        name="Poll / sync CLOB prices",
        max_instances=1,
        misfire_grace_time=15,
    )

    return scheduler
