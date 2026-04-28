"""
FastAPI application entry point.

Lifespan:
  startup  → run DB migrations, start APScheduler, start Telegram bot polling,
             trigger immediate first market refresh
  shutdown → stop scheduler + bot
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from app.config import settings
from app.api.health import router as health_router

logger = logging.getLogger(__name__)

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Sports Win Bot starting...")

    # Start scheduler
    from app.workers.scheduler import build_scheduler
    scheduler = build_scheduler()
    scheduler.start()

    # Trigger immediate market refresh so first /markets command works right away
    from app.workers.jobs import job_refresh_markets
    import asyncio
    asyncio.get_event_loop().create_task(job_refresh_markets())

    # Start Telegram bot (polling)
    from app.bot.telegram_bot import build_application
    tg_app = await build_application()
    if tg_app:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling started")

    yield  # ← app is running

    # Shutdown
    logger.info("Sports Win Bot shutting down...")
    scheduler.shutdown(wait=False)
    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


app = FastAPI(
    title="Sports Win Probability Bot",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health_router)
