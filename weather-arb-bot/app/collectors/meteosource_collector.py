import logging
from datetime import date
from typing import Optional

from app.collectors.base import BaseCollector
from app.models.forecast import Forecast

logger = logging.getLogger(__name__)
METEOSOURCE_URL = "https://www.meteosource.com/api/v1/free/point"


class MeteosourceCollector(BaseCollector):
    name = "meteosource"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    async def collect(self, lat: float, lon: float, forecast_date: Optional[date] = None) -> Optional[dict]:
        if not self.api_key:
            return None
        target = forecast_date or date.today()
        try:
            resp = await self._get(METEOSOURCE_URL, params={
                "lat": lat,
                "lon": lon,
                "sections": "daily",
                "units": "us",
                "key": self.api_key,
            })
            data = resp.json()
            days = data.get("daily", {}).get("data", [])
            if not days:
                return None
            target_str = str(target)
            for day in days:
                if day.get("day", "") == target_str:
                    all_day = day.get("all_day", {})
                    high = all_day.get("temperature_max")
                    low = all_day.get("temperature_min")
                    if high is None:
                        return None
                    return {
                        "predicted_high_f": round(high),
                        "predicted_low_f": round(low) if low is not None else None,
                    }
            return None
        except Exception as e:
            logger.error(f"Meteosource fetch failed ({lat},{lon}): {e}")
            return None

    async def collect_and_store(self, city_id, lat, lon, forecast_date, db):
        parsed = await self.collect(lat, lon, forecast_date)
        if not parsed:
            return None
        forecast = Forecast(
            city_id=city_id,
            source="meteosource",
            forecast_for_date=forecast_date,
            predicted_high_f=parsed.get("predicted_high_f"),
            predicted_low_f=parsed.get("predicted_low_f"),
            raw_data=parsed,
        )
        db.add(forecast)
        await db.commit()
        return parsed
