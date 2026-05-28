import logging
from datetime import date
from typing import Optional

from app.collectors.base import BaseCollector
from app.models.forecast import Forecast

logger = logging.getLogger(__name__)
TOMORROW_URL = "https://api.tomorrow.io/v4/weather/forecast"


class TomorrowioCollector(BaseCollector):
    name = "tomorrowio"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    async def collect(self, lat: float, lon: float, forecast_date: Optional[date] = None) -> Optional[dict]:
        if not self.api_key:
            return None
        target = forecast_date or date.today()
        try:
            resp = await self._get(TOMORROW_URL, params={
                "location": f"{lat},{lon}",
                "fields": "temperatureMax,temperatureMin",
                "timesteps": "1d",
                "units": "imperial",
                "apikey": self.api_key,
            })
            data = resp.json()
            timelines = data.get("timelines", {}).get("daily", [])
            if not timelines:
                return None
            target_str = str(target)
            for entry in timelines:
                if entry.get("time", "")[:10] == target_str:
                    vals = entry.get("values", {})
                    high = vals.get("temperatureMax")
                    low = vals.get("temperatureMin")
                    if high is None:
                        return None
                    return {
                        "predicted_high_f": round(high),
                        "predicted_low_f": round(low) if low is not None else None,
                    }
            return None
        except Exception as e:
            logger.error(f"Tomorrow.io fetch failed ({lat},{lon}): {e}")
            return None

    async def collect_and_store(self, city_id, lat, lon, forecast_date, db):
        parsed = await self.collect(lat, lon, forecast_date)
        if not parsed:
            return None
        forecast = Forecast(
            city_id=city_id,
            source="tomorrowio",
            forecast_for_date=forecast_date,
            predicted_high_f=parsed.get("predicted_high_f"),
            predicted_low_f=parsed.get("predicted_low_f"),
            raw_data=parsed,
        )
        db.add(forecast)
        await db.commit()
        return parsed
