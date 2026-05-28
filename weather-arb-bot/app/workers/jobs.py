import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.city import City
from app.models.market import Market, MarketOutcome
from app.collectors.metar_collector import MetarCollector
from app.collectors.wunderground_collector import WundergroundCollector
from app.collectors.nws_collector import NWSCollector
from app.collectors.gfs_collector import GFSCollector
from app.collectors.hrrr_collector import HRRRCollector
from app.collectors.tomorrowio_collector import TomorrowioCollector
from app.collectors.meteosource_collector import MeteosourceCollector
from app.collectors.pirep_collector import PirepCollector
from app.collectors.polymarket_collector import PolymarketCollector
from app.analyzers.opportunity_detector import detect_opportunities
from app.bot.telegram_bot import send_opportunity_alert
from app.config import settings

logger = logging.getLogger(__name__)

metar_col = MetarCollector()
wunder_col = WundergroundCollector()
nws_col = NWSCollector()
gfs_col = GFSCollector()
hrrr_col = HRRRCollector()
tomorrowio_col = TomorrowioCollector(api_key=settings.tomorrowio_api_key)
meteosource_col = MeteosourceCollector(api_key=settings.meteosource_api_key)
pirep_col = PirepCollector()
poly_col = PolymarketCollector()


async def job_fetch_metars():
    """Fetch METAR data for all active cities (primary + reference stations)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(City).where(City.active == True))
        cities = result.scalars().all()
        for city in cities:
            try:
                await metar_col.collect_and_store(city.primary_icao, db)
                if city.reference_icao:
                    await metar_col.collect_and_store(city.reference_icao, db)
            except Exception as e:
                logger.error(f"METAR job failed for city {city.name}: {e}")


async def job_fetch_wunderground():
    """Fetch Wunderground forecasts for all active cities."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(City).where(City.active == True))
        cities = result.scalars().all()
        today = date.today()
        for city in cities:
            try:
                await wunder_col.collect_and_store(city.id, city.wunderground_url, today, db)
            except Exception as e:
                logger.error(f"Wunderground job failed for {city.name}: {e}")


async def job_fetch_nws():
    """Fetch NWS forecasts for all active cities that have coordinates."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(City).where(City.active == True))
        cities = result.scalars().all()
        today = date.today()
        for city in cities:
            if city.nws_lat is None or city.nws_lon is None:
                continue
            try:
                await nws_col.collect_and_store(
                    city.id, float(city.nws_lat), float(city.nws_lon), today, db
                )
            except Exception as e:
                logger.error(f"NWS job failed for {city.name}: {e}")


async def job_fetch_models():
    """Fetch GFS, ECMWF, and HRRR model data for all active cities."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(City).where(City.active == True))
        cities = result.scalars().all()
        today = date.today()
        for city in cities:
            if city.nws_lat is None or city.nws_lon is None:
                continue
            lat, lon = float(city.nws_lat), float(city.nws_lon)
            for model in ("gfs", "ecmwf"):
                try:
                    await gfs_col.collect_and_store(city.id, lat, lon, today, db, model)
                except Exception as e:
                    logger.error(f"{model} job failed for {city.name}: {e}")
            try:
                await hrrr_col.collect_and_store(city.id, lat, lon, today, db)
            except Exception as e:
                logger.error(f"HRRR job failed for {city.name}: {e}")


async def job_fetch_external_forecasts():
    """Fetch Tomorrow.io and Meteosource forecasts for the next 3 days."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(City).where(City.active == True))
        cities = result.scalars().all()
        today = date.today()
        for city in cities:
            if city.nws_lat is None or city.nws_lon is None:
                continue
            lat, lon = float(city.nws_lat), float(city.nws_lon)
            for day_offset in range(3):
                target = today + timedelta(days=day_offset)
                try:
                    await tomorrowio_col.collect_and_store(city.id, lat, lon, target, db)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Tomorrow.io job failed for {city.name} day+{day_offset}: {e}")
                try:
                    await meteosource_col.collect_and_store(city.id, lat, lon, target, db)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Meteosource job failed for {city.name} day+{day_offset}: {e}")


async def job_fetch_pireps():
    """Fetch PIREPs near all active city primary stations."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(City).where(City.active == True))
        cities = result.scalars().all()
        for city in cities:
            try:
                await pirep_col.collect_and_store(city.primary_icao, db)
            except Exception as e:
                logger.error(f"PIREP job failed for {city.name}: {e}")


async def job_fetch_polymarket():
    """Fetch current Polymarket prices for all tracked outcomes."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MarketOutcome)
            .join(Market)
            .where(Market.resolved == False)
        )
        outcomes = result.scalars().all()
        for outcome in outcomes:
            market_result = await db.execute(
                select(Market).where(Market.id == outcome.market_id)
            )
            market = market_result.scalar_one_or_none()
            if not market or not market.external_id:
                continue
            try:
                token_id = f"{market.external_id}_{outcome.bucket_label}"
                await poly_col.collect_and_store(outcome.id, token_id, db)
            except Exception as e:
                logger.error(f"Polymarket job failed for outcome {outcome.id}: {e}")


async def job_run_analyzer():
    """Run opportunity detection and send Telegram alerts for new finds."""
    async with AsyncSessionLocal() as db:
        try:
            opportunities = await detect_opportunities(db)
            for opp in opportunities:
                try:
                    await send_opportunity_alert(opp, db)
                except Exception as e:
                    logger.error(f"Failed to send alert for opportunity {opp.id}: {e}")
        except Exception as e:
            logger.error(f"Analyzer job failed: {e}", exc_info=True)
