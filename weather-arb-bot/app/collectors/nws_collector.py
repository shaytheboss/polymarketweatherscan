import logging
from datetime import date
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.models.forecast import Forecast

logger = logging.getLogger(__name__)
NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"


class NWSCollector(BaseCollector):
    name = "nws"

    async def _get_forecast_url(self, lat, lon):
        try:
            resp = await self._get(NWS_POINTS_URL.format(lat=lat, lon=lon))
            return resp.json()["properties"]["forecast"]
        except Exception as e:
            logger.error(f"NWS points lookup failed for ({lat},{lon}): {e}")
            return None

    async def collect(self, lat: float, lon: float) -> Optional[dict]:
        forecast_url = await self._get_forecast_url(lat, lon)
        if not forecast_url:
            return None
        try:
            resp = await self._get(forecast_url)
            periods = resp.json()["properties"]["periods"]
            today = str(date.today())
            result: dict = {"raw_periods": periods}
            for period in periods:
                start = period.get("startTime", "")[:10]
                if start == today:
                    if period.get("isDaytime"):
                        result["predicted_high_f"] = period.get("temperature")
                        result["conditions"] = period.get("shortForecast")
                    else:
                        result["predicted_low_f"] = period.get("temperature")
                    if "predicted_high_f" in result and "predicted_low_f" in result:
                        break
            return result
        except Exception as e:
            logger.error(f"NWS forecast fetch failed: {e}")
            return None

    async def collect_and_store(self, city_id, lat, lon, forecast_date, db):
        parsed = await self.collect(lat, lon)
        if not parsed:
            return None
        forecast = Forecast(city_id=city_id, source="nws", forecast_for_date=forecast_date, predicted_high_f=parsed.get("predicted_high_f"), predicted_low_f=parsed.get("predicted_low_f"), conditions=parsed.get("conditions"), raw_data={"periods": parsed.get("raw_periods", [])[:3]})
        db.add(forecast)
        await db.commit()
        return parsed
