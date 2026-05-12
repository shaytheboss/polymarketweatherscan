"""
Open-Meteo Ensemble collector.

Unlike the deterministic GFS/ECMWF endpoint, the Ensemble API returns the
hourly forecast for every individual ensemble member (typically ~30 for GFS,
~50 for ECMWF IFS). We compute each member's local-day temperature high and
expose the empirical distribution — this is the most useful signal for
bucket-style Polymarket markets because the probability that the resolved
high falls in a bucket can be read straight off the member histogram.
"""
import logging
import statistics
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.models.forecast import Forecast
from app.utils.icao_lookup import lookup_icao

logger = logging.getLogger(__name__)

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Open-Meteo model identifiers for ensemble runs.
MODEL_ALIASES = {
    "gfs": "gfs025",         # GFS ensemble, ~30 members at 0.25°
    "ecmwf": "ifs04",        # ECMWF IFS ensemble, ~50 members at 0.4°
    "icon": "icon_seamless",
}


def _member_series(hourly: dict, var: str) -> list[list[Optional[float]]]:
    """
    Pull every member series for a given variable out of the hourly payload.

    Open-Meteo encodes the control run as e.g. ``temperature_2m`` and members
    as ``temperature_2m_member01`` … ``temperature_2m_memberNN``.
    """
    series: list[list[Optional[float]]] = []
    if var in hourly and hourly[var]:
        series.append(hourly[var])
    i = 1
    while True:
        key = f"{var}_member{i:02d}"
        if key not in hourly:
            break
        series.append(hourly[key])
        i += 1
    return series


def _local_day_max(
    times_iso: list[str],
    values: list[Optional[float]],
    target_date: date,
    tz: ZoneInfo,
) -> Optional[float]:
    """Max value across the target local calendar date."""
    best: Optional[float] = None
    for t, v in zip(times_iso, values):
        if v is None:
            continue
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
        if dt.date() != target_date:
            continue
        if best is None or v > best:
            best = v
    return best


class EnsembleCollector(BaseCollector):
    name = "ensemble"

    async def collect(
        self,
        lat: float,
        lon: float,
        model: str = "gfs",
        forecast_date: Optional[date] = None,
        tz_name: str = "America/Los_Angeles",
    ) -> Optional[dict]:
        """
        Fetch ensemble forecast and reduce to per-member daily highs.

        Returns:
            {
              "members_high_f": [list of per-member max-temp values for the day],
              "mean_high_f", "median_high_f", "stdev_high_f",
              "min_high_f", "max_high_f", "p10_high_f", "p90_high_f",
              "model", "num_members",
            }
        """
        wmo_model = MODEL_ALIASES.get(model, model)
        target_date = forecast_date or date.today()
        tz = ZoneInfo(tz_name)

        try:
            resp = await self._get(
                ENSEMBLE_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "temperature_2m",
                    "temperature_unit": "fahrenheit",
                    "forecast_days": 3,
                    "models": wmo_model,
                    "timezone": tz_name,
                },
            )
            data = resp.json()
        except Exception as e:
            logger.error(f"Ensemble fetch failed ({model}): {e}")
            return None

        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return None

        member_series = _member_series(hourly, "temperature_2m")
        if not member_series:
            return None

        member_highs: list[float] = []
        for series in member_series:
            high = _local_day_max(times, series, target_date, tz)
            if high is not None:
                member_highs.append(high)

        if not member_highs:
            return None

        sorted_highs = sorted(member_highs)
        n = len(sorted_highs)

        def _pct(p: float) -> float:
            # Linear-interpolation percentile to stay sane for small N.
            if n == 1:
                return sorted_highs[0]
            idx = p * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            frac = idx - lo
            return sorted_highs[lo] * (1 - frac) + sorted_highs[hi] * frac

        return {
            "model": model,
            "num_members": n,
            "members_high_f": [round(v, 1) for v in member_highs],
            "mean_high_f": round(statistics.fmean(member_highs), 1),
            "median_high_f": round(statistics.median(member_highs), 1),
            "stdev_high_f": round(statistics.pstdev(member_highs), 2) if n > 1 else 0.0,
            "min_high_f": round(min(member_highs), 1),
            "max_high_f": round(max(member_highs), 1),
            "p10_high_f": round(_pct(0.10), 1),
            "p90_high_f": round(_pct(0.90), 1),
        }

    async def collect_for_icao(
        self,
        icao: str,
        forecast_date: Optional[date] = None,
        model: str = "gfs",
    ) -> Optional[dict]:
        """Convenience wrapper that uses the ICAO lookup table for coords + tz."""
        meta = lookup_icao(icao)
        if not meta:
            logger.warning(f"No ICAO metadata for {icao}; cannot fetch ensemble")
            return None
        return await self.collect(
            lat=meta["lat"],
            lon=meta["lon"],
            model=model,
            forecast_date=forecast_date,
            tz_name=meta.get("timezone", "UTC"),
        )

    async def collect_and_store(
        self,
        city_id: int,
        lat: float,
        lon: float,
        forecast_date: date,
        db: AsyncSession,
        model: str = "gfs",
        tz_name: str = "UTC",
    ) -> Optional[dict]:
        parsed = await self.collect(lat, lon, model, forecast_date, tz_name)
        if not parsed:
            return None

        forecast = Forecast(
            city_id=city_id,
            source=f"ensemble_{model}",
            forecast_for_date=forecast_date,
            predicted_high_f=int(round(parsed["median_high_f"])),
            predicted_low_f=None,
            raw_data=parsed,
        )
        db.add(forecast)
        await db.commit()
        logger.info(
            f"Ensemble {model} forecast stored for city {city_id}: "
            f"n={parsed['num_members']} median={parsed['median_high_f']}°F "
            f"σ={parsed['stdev_high_f']}"
        )
        return parsed


def bucket_probability_from_members(
    members_high_f: list[float],
    bucket_min: Optional[int],
    bucket_max: Optional[int],
) -> Optional[float]:
    """
    Empirical probability that the daily high lands in the given bucket,
    using a half-degree inclusive interpretation: a bucket "64–65" covers
    [63.5, 65.5) which matches how Wunderground rounds reported highs.
    """
    if not members_high_f:
        return None
    if bucket_min is None and bucket_max is None:
        return None

    lo = bucket_min - 0.5 if bucket_min is not None else float("-inf")
    hi = bucket_max + 0.5 if bucket_max is not None else float("inf")

    in_bucket = sum(1 for v in members_high_f if lo <= v < hi)
    return in_bucket / len(members_high_f)
