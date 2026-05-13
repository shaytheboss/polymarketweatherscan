import logging
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.models.pirep import Pirep
from app.utils.pirep_parser import parse_pirep_text

logger = logging.getLogger(__name__)
PIREP_URL = "https://aviationweather.gov/api/data/pirep"


class PirepCollector(BaseCollector):
    name = "pirep"

    async def collect(self, icao: str, distance_nm: int = 100, age_hours: int = 2) -> List[dict]:
        try:
            resp = await self._get(PIREP_URL, params={"id": icao, "distance": distance_nm, "age": age_hours, "format": "json"})
            data = resp.json()
            if not data:
                return []
            return [p for item in data for p in [parse_pirep_text(item.get("rawOb", ""), item)] if p]
        except Exception as e:
            logger.error(f"PIREP fetch failed for {icao}: {e}")
            return []

    async def collect_and_store(self, icao: str, db: AsyncSession, distance_nm: int = 100) -> List[dict]:
        pireps = await self.collect(icao, distance_nm)
        stored = 0
        for p in pireps:
            try:
                db.add(Pirep(near_icao=icao, observed_at=p["observed_at"], location_offset=p.get("location_offset"), flight_level_ft=p.get("flight_level_ft"), aircraft_type=p.get("aircraft_type"), temperature_c=p.get("temperature_c"), wind_direction=p.get("wind_direction"), wind_speed_kt=p.get("wind_speed_kt"), turbulence=p.get("turbulence"), icing=p.get("icing"), raw_pirep=p.get("raw")))
                stored += 1
            except Exception as e:
                logger.warning(f"Failed to store PIREP: {e}")
        if stored:
            await db.commit()
        return pireps
