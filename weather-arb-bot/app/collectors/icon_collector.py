import logging
from datetime import date
from typing import Optional

from app.collectors.base import BaseCollector
from app.models.forecast import Forecast

logger = logging.getLogger(__name__)
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
ICON_MAX_DAYS_AHEAD = 5


class ICONCollector(BaseCollector):
    """German DWD ICON model (global), via Open-Meteo. Free, no key."""
    name = "icon"

    async def collect(self, lat: float, lon: float, forecast_date: Optional[date] = None) -> Optional[dict]:
        target = forecast_date or date.today()
        days_ahead = (target - date.today()).days
        if days_ahead < 0 or days_ahead > ICON_MAX_DAYS_AHEAD:
            return None
        try:
            resp = await self._get(OPEN_METEO_URL, params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "forecast_days": ICON_MAX_DAYS_AHEAD + 1,
                "models": "icon_seamless",
                "timezone": "auto",
            })
            data = resp.json()
            daily = data.get("daily", {})
            if not daily or not daily.get("time"):
                return None
            target_str = str(target)
            idx = next((i for i, t in enumerate(daily["time"]) if t == target_str), None)
            if idx is None:
                return None
            return {
                "predicted_high_f": round(daily["temperature_2m_max"][idx]),
                "predicted_low_f": round(daily["temperature_2m_min"][idx]),
                "model": "icon",
            }
        except Exception as e:
            logger.error(f"ICON fetch failed ({lat},{lon}): {e}")
            return None

    async def collect_and_store(self, city_id, lat, lon, forecast_date, db):
        parsed = await self.collect(lat, lon, forecast_date)
        if not parsed:
            return None
        forecast = Forecast(
            city_id=city_id,
            source="icon",
            forecast_for_date=forecast_date,
            predicted_high_f=parsed.get("predicted_high_f"),
            predicted_low_f=parsed.get("predicted_low_f"),
            raw_data=parsed,
        )
        db.add(forecast)
        await db.commit()
        return parsed
