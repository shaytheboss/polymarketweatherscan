import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.metar import MetarObservation
from app.models.forecast import Forecast
from app.models.pirep import Pirep
from app.models.market import MarketPrice, MarketOutcome

logger = logging.getLogger(__name__)


class SignalAggregator:
    """
    Gathers all current signals for a market outcome into a structured dict.
    """

    async def aggregate(
        self,
        db: AsyncSession,
        city_id: int,
        primary_icao: str,
        reference_icao: Optional[str],
        outcome: MarketOutcome,
    ) -> dict:
        signals = {}

        # Latest METAR for primary station
        signals["primary_metar"] = await self._latest_metar(db, primary_icao)

        # Latest METAR for reference/coastal station
        if reference_icao:
            signals["reference_metar"] = await self._latest_metar(db, reference_icao)

        # METAR trend (last 3 hours)
        signals["metar_trend"] = await self._metar_trend(db, primary_icao, hours=3)

        # Latest Wunderground forecast
        signals["wunderground_forecast"] = await self._latest_forecast(
            db, city_id, "wunderground"
        )

        # Model forecasts
        signals["gfs_forecast"] = await self._latest_forecast(db, city_id, "gfs")
        signals["ecmwf_forecast"] = await self._latest_forecast(db, city_id, "ecmwf")

        # Ensemble forecasts (carry the per-member distribution)
        signals["ensemble_gfs"] = await self._latest_forecast(db, city_id, "ensemble_gfs")
        signals["ensemble_ecmwf"] = await self._latest_forecast(db, city_id, "ensemble_ecmwf")

        # Recent PIREPs near primary station
        signals["pireps"] = await self._recent_pireps(db, primary_icao, hours=2)

        # Current market price
        signals["market_price"] = await self._latest_price(db, outcome.id)

        # Market price trend (last hour)
        signals["price_trend"] = await self._price_trend(db, outcome.id, minutes=60)

        return signals

    async def _latest_metar(self, db: AsyncSession, icao: str) -> Optional[dict]:
        result = await db.execute(
            select(MetarObservation)
            .where(MetarObservation.icao == icao)
            .order_by(desc(MetarObservation.observed_at))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "temperature_f": float(row.temperature_f) if row.temperature_f else None,
            "dew_point_f": float(row.dew_point_f) if row.dew_point_f else None,
            "humidity_pct": row.humidity_pct,
            "wind_direction": row.wind_direction,
            "wind_speed_kt": row.wind_speed_kt,
            "wind_gust_kt": row.wind_gust_kt,
            "pressure_hg": float(row.pressure_hg) if row.pressure_hg else None,
            "observed_at": row.observed_at.isoformat(),
        }

    async def _metar_trend(
        self, db: AsyncSession, icao: str, hours: int = 3
    ) -> Optional[dict]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await db.execute(
            select(MetarObservation)
            .where(MetarObservation.icao == icao, MetarObservation.observed_at >= since)
            .order_by(MetarObservation.observed_at)
        )
        rows = result.scalars().all()
        if len(rows) < 2:
            return None

        temps = [float(r.temperature_f) for r in rows if r.temperature_f is not None]
        dews = [float(r.dew_point_f) for r in rows if r.dew_point_f is not None]

        if len(temps) < 2:
            return None

        hours_span = (rows[-1].observed_at - rows[0].observed_at).total_seconds() / 3600
        if hours_span == 0:
            return None

        temp_rate = (temps[-1] - temps[0]) / hours_span
        dew_rate = (dews[-1] - dews[0]) / hours_span if len(dews) >= 2 else None

        return {
            "temp_rate_per_hour": round(temp_rate, 2),
            "dew_rate_per_hour": round(dew_rate, 2) if dew_rate is not None else None,
            "current_temp_f": temps[-1],
            "oldest_temp_f": temps[0],
            "span_hours": round(hours_span, 2),
        }

    async def _latest_forecast(
        self, db: AsyncSession, city_id: int, source: str
    ) -> Optional[dict]:
        result = await db.execute(
            select(Forecast)
            .where(Forecast.city_id == city_id, Forecast.source == source)
            .order_by(desc(Forecast.retrieved_at))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        out = {
            "predicted_high_f": row.predicted_high_f,
            "predicted_low_f": row.predicted_low_f,
            "conditions": row.conditions,
            "retrieved_at": row.retrieved_at.isoformat(),
        }
        # Ensemble rows carry the per-member distribution in raw_data; pass it
        # through so the probability estimator can read it directly.
        if row.raw_data and source.startswith("ensemble"):
            raw = row.raw_data
            out.update(
                {
                    "members_high_f": raw.get("members_high_f"),
                    "mean_high_f": raw.get("mean_high_f"),
                    "median_high_f": raw.get("median_high_f"),
                    "stdev_high_f": raw.get("stdev_high_f"),
                    "p10_high_f": raw.get("p10_high_f"),
                    "p90_high_f": raw.get("p90_high_f"),
                    "num_members": raw.get("num_members"),
                }
            )
        return out

    async def _recent_pireps(
        self, db: AsyncSession, icao: str, hours: int = 2
    ) -> list:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await db.execute(
            select(Pirep)
            .where(Pirep.near_icao == icao, Pirep.observed_at >= since)
            .order_by(desc(Pirep.observed_at))
            .limit(20)
        )
        rows = result.scalars().all()
        return [
            {
                "flight_level_ft": r.flight_level_ft,
                "temperature_c": float(r.temperature_c) if r.temperature_c else None,
                "wind_direction": r.wind_direction,
                "wind_speed_kt": r.wind_speed_kt,
                "turbulence": r.turbulence,
                "observed_at": r.observed_at.isoformat(),
            }
            for r in rows
        ]

    async def _latest_price(
        self, db: AsyncSession, outcome_id: int
    ) -> Optional[dict]:
        result = await db.execute(
            select(MarketPrice)
            .where(MarketPrice.outcome_id == outcome_id)
            .order_by(desc(MarketPrice.timestamp))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "yes_price": float(row.yes_price),
            "no_price": float(row.no_price),
            "timestamp": row.timestamp.isoformat(),
        }

    async def _price_trend(
        self, db: AsyncSession, outcome_id: int, minutes: int = 60
    ) -> Optional[dict]:
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        result = await db.execute(
            select(MarketPrice)
            .where(MarketPrice.outcome_id == outcome_id, MarketPrice.timestamp >= since)
            .order_by(MarketPrice.timestamp)
        )
        rows = result.scalars().all()
        if len(rows) < 2:
            return None
        oldest = float(rows[0].yes_price)
        newest = float(rows[-1].yes_price)
        return {
            "change": round(newest - oldest, 4),
            "oldest_price": oldest,
            "newest_price": newest,
            "num_ticks": len(rows),
        }
