#!/usr/bin/env python3
"""
Standalone script: max temperature forecast + Polymarket comparison.

Usage (no API keys required — all free endpoints):
  python scripts/forecast_temp.py --city "Tel Aviv"
  python scripts/forecast_temp.py --city "New York" --date 2026-05-10
  python scripts/forecast_temp.py --lat 32.09 --lon 34.78 --date 2026-05-12
  python scripts/forecast_temp.py --city "Los Angeles" --celsius

Run from within weather-arb-bot/  (uses aiohttp already in requirements.txt).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import aiohttp

# ---------------------------------------------------------------------------
# Free API endpoints (no keys)
# ---------------------------------------------------------------------------
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NWS_POINTS_URL = "https://api.weather.gov/points/{lat},{lon}"
GAMMA_API_URL = "https://gamma-api.polymarket.com/events"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://polymarket.com/",
    "Origin": "https://polymarket.com",
}

# Polymarket tag IDs known to contain weather/climate markets
WEATHER_TAG_IDS = [1388, 1389, 1390, 100069]  # approximate — searched by query if empty


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

async def geocode(session: aiohttp.ClientSession, city: str) -> Optional[tuple[float, float, str]]:
    """Return (lat, lon, resolved_name) or None."""
    async with session.get(
        GEOCODING_URL,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        data = await resp.json(content_type=None)
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    name = f"{r.get('name', city)}, {r.get('country', '')}"
    return r["latitude"], r["longitude"], name


# ---------------------------------------------------------------------------
# Open-Meteo: GFS + ECMWF models
# ---------------------------------------------------------------------------

async def fetch_open_meteo(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
    target_date: date,
    model: str = "gfs_seamless",
) -> Optional[dict]:
    """
    Returns {"max_c": float, "min_c": float, "max_f": float, "model": model}
    for the target date, or None on failure.
    """
    # Open-Meteo requires a date range — request 7 days so target always falls in window
    start_str = target_date.strftime("%Y-%m-%d")
    end_str = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        async with session.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "celsius",
                "forecast_days": 16,
                "start_date": start_str,
                "end_date": end_str,
                "models": model,
                "timezone": "auto",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as e:
        print(f"  [open-meteo/{model}] error: {e}", file=sys.stderr)
        return None

    daily = data.get("daily") or {}
    times = daily.get("time") or []
    maxes = daily.get("temperature_2m_max") or []
    mins = daily.get("temperature_2m_min") or []
    target_str = target_date.strftime("%Y-%m-%d")

    for i, t in enumerate(times):
        if t == target_str:
            max_c = round(maxes[i], 1) if i < len(maxes) and maxes[i] is not None else None
            min_c = round(mins[i], 1) if i < len(mins) and mins[i] is not None else None
            if max_c is None:
                return None
            max_f = round(max_c * 9 / 5 + 32, 1)
            min_f = round(min_c * 9 / 5 + 32, 1) if min_c is not None else None
            return {"max_c": max_c, "min_c": min_c, "max_f": max_f, "min_f": min_f, "model": model}
    return None


# ---------------------------------------------------------------------------
# NWS (US only)
# ---------------------------------------------------------------------------

def _is_us_coords(lat: float, lon: float) -> bool:
    return 24 <= lat <= 72 and -180 <= lon <= -60


async def fetch_nws(
    session: aiohttp.ClientSession, lat: float, lon: float, target_date: date
) -> Optional[dict]:
    if not _is_us_coords(lat, lon):
        return None
    try:
        async with session.get(
            NWS_POINTS_URL.format(lat=lat, lon=lon),
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"Accept": "application/geo+json"},
        ) as resp:
            meta = await resp.json(content_type=None)
        forecast_url = meta.get("properties", {}).get("forecast")
        if not forecast_url:
            return None

        async with session.get(
            forecast_url,
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"Accept": "application/geo+json"},
        ) as resp2:
            fdata = await resp2.json(content_type=None)
        periods = fdata.get("properties", {}).get("periods") or []
        target_str = target_date.strftime("%Y-%m-%d")
        for period in periods:
            start = period.get("startTime", "")[:10]
            if start == target_str and period.get("isDaytime"):
                temp_f = period.get("temperature")
                unit = period.get("temperatureUnit", "F")
                if temp_f is None:
                    continue
                if unit == "C":
                    max_c = float(temp_f)
                    max_f = round(max_c * 9 / 5 + 32, 1)
                else:
                    max_f = float(temp_f)
                    max_c = round((max_f - 32) * 5 / 9, 1)
                return {"max_c": max_c, "max_f": max_f, "model": "NWS", "conditions": period.get("shortForecast", "")}
    except Exception as e:
        print(f"  [NWS] error: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Polymarket: search for weather temperature markets
# ---------------------------------------------------------------------------

async def fetch_polymarket_weather_markets(
    session: aiohttp.ClientSession,
    city_name: str,
    target_date: date,
) -> list[dict]:
    """
    Search Gamma API for weather temperature markets matching the city + date.
    Returns list of simplified market dicts.
    """
    city_short = city_name.split(",")[0].strip()
    results = []

    # Search by city name keyword
    try:
        async with session.get(
            GAMMA_API_URL,
            params={
                "q": city_short,
                "active": "true",
                "closed": "false",
                "limit": 50,
            },
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as e:
        print(f"  [Polymarket] search error: {e}", file=sys.stderr)
        return []

    events = data if isinstance(data, list) else data.get("data", [])
    date_str = target_date.strftime("%Y-%m-%d")
    date_strs = [
        target_date.strftime("%B %d"),
        target_date.strftime("%b %d"),
        target_date.strftime("%Y-%m-%d"),
        target_date.strftime("%-d %B"),
        str(target_date.day),
    ]

    for event in events:
        event_slug = event.get("slug", "")
        for market in event.get("markets", []):
            question = market.get("question", "")
            # Filter: question must mention temperature/weather/high AND the city
            if not any(kw in question.lower() for kw in ("temperature", "high temp", "degrees", "°", "fahrenheit", "celsius", "weather")):
                continue
            if city_short.lower() not in question.lower():
                continue

            # Parse current prices
            raw_prices = market.get("outcomePrices", "[0.5,0.5]")
            prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
            yes_price = float(prices[0]) if prices else 0.5

            outcomes = market.get("outcomes", "[]")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []

            results.append({
                "question": question,
                "yes_price": yes_price,
                "outcomes": outcomes,
                "url": f"https://polymarket.com/event/{event_slug}",
                "condition_id": market.get("conditionId", ""),
            })

    return results


# ---------------------------------------------------------------------------
# Simple probability estimate: P(max_temp >= threshold)
# ---------------------------------------------------------------------------

def _bucket_probability(forecast_max_c: float, bucket_min_c: Optional[float], bucket_max_c: Optional[float]) -> float:
    """
    Gaussian-based probability that actual max temp falls in [bucket_min, bucket_max].
    Uses forecast uncertainty of ±2°C (typical GFS day-ahead skill).
    """
    import math
    sigma = 2.5  # typical forecast uncertainty in °C

    def cdf(x: float) -> float:
        # Normal CDF approximation
        return 0.5 * (1 + math.erf(x / (sigma * math.sqrt(2))))

    lo = cdf(forecast_max_c - (bucket_min_c if bucket_min_c is not None else -999))
    hi = cdf(forecast_max_c - (bucket_max_c if bucket_max_c is not None else 999))
    return max(0.01, min(0.99, lo - hi))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def _parse_temp_from_question(question: str) -> Optional[tuple[Optional[float], Optional[float]]]:
    """Try to extract bucket range from question text. Returns (min_c, max_c) or None."""
    import re
    # Patterns like "above 85°F", "below 70°F", "between 75 and 85°F", "exceed 90 degrees"
    q = question.lower()
    is_f = "°f" in q or "fahrenheit" in q or ("degree" in q and "celsius" not in q)
    is_c = "°c" in q or "celsius" in q

    # "above X" / "exceed X" / "over X"
    m = re.search(r"(above|exceed|over|higher than|at least)\s*(\d+\.?\d*)", q)
    if m:
        v = float(m.group(2))
        v_c = (v - 32) * 5 / 9 if is_f else v
        return v_c, None

    # "below X" / "under X"
    m = re.search(r"(below|under|less than|lower than|at most)\s*(\d+\.?\d*)", q)
    if m:
        v = float(m.group(2))
        v_c = (v - 32) * 5 / 9 if is_f else v
        return None, v_c

    # "between X and Y"
    m = re.search(r"between\s*(\d+\.?\d*)\s*(?:and|–|-)\s*(\d+\.?\d*)", q)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if is_f:
            return (lo - 32) * 5 / 9, (hi - 32) * 5 / 9
        return lo, hi

    return None


async def main():
    parser = argparse.ArgumentParser(description="Temperature forecast + Polymarket comparison")
    parser.add_argument("--city", help="City name (e.g. 'Tel Aviv', 'New York')")
    parser.add_argument("--lat", type=float, help="Latitude (use with --lon instead of --city)")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--date", default=str(date.today()), help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--celsius", action="store_true", help="Show temperatures in Celsius (default: Fahrenheit)")
    parser.add_argument("--no-polymarket", action="store_true", help="Skip Polymarket lookup")
    args = parser.parse_args()

    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date: {args.date}. Use YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)

    if (target_date - date.today()).days > 15:
        print("Warning: Open-Meteo only forecasts up to 16 days ahead.", file=sys.stderr)

    async with aiohttp.ClientSession() as session:
        # Step 1: resolve coordinates
        lat, lon, city_display = None, None, args.city or "custom location"
        if args.lat is not None and args.lon is not None:
            lat, lon = args.lat, args.lon
        elif args.city:
            print(f"Geocoding '{args.city}'...")
            geo = await geocode(session, args.city)
            if not geo:
                print(f"Could not geocode '{args.city}'.", file=sys.stderr)
                sys.exit(1)
            lat, lon, city_display = geo
            print(f"  → {city_display}  ({lat:.4f}, {lon:.4f})")
        else:
            parser.print_help()
            sys.exit(1)

        print(f"\n{'='*60}")
        print(f"  Temperature forecast for: {city_display}")
        print(f"  Date: {target_date}  ({target_date.strftime('%A, %B %d %Y')})")
        print(f"{'='*60}\n")

        # Step 2: fetch from all model sources in parallel
        gfs_task = asyncio.create_task(fetch_open_meteo(session, lat, lon, target_date, "gfs_seamless"))
        ecmwf_task = asyncio.create_task(fetch_open_meteo(session, lat, lon, target_date, "ecmwf_ifs025"))
        nws_task = asyncio.create_task(fetch_nws(session, lat, lon, target_date))

        gfs_result, ecmwf_result, nws_result = await asyncio.gather(gfs_task, ecmwf_task, nws_task)

        def temp_str(c: Optional[float]) -> str:
            if c is None:
                return "N/A"
            if args.celsius:
                return f"{c:.1f}°C"
            return f"{round(c * 9/5 + 32, 1):.1f}°F"

        print("  📡 Model Forecasts (Max Temperature):")
        print(f"  {'Source':<14} {'Max':>10}   {'Min':>10}")
        print(f"  {'-'*40}")

        all_maxes_c: list[float] = []
        for label, result in [("GFS (OMM)", gfs_result), ("ECMWF (OMM)", ecmwf_result), ("NWS", nws_result)]:
            if result:
                max_c = result["max_c"]
                min_c = result.get("min_c")
                all_maxes_c.append(max_c)
                extra = f"  ({result.get('conditions', '')})" if result.get("conditions") else ""
                print(f"  {label:<14} {temp_str(max_c):>10}   {temp_str(min_c):>10}{extra}")
            else:
                print(f"  {label:<14}  {'—':>10}")

        # Ensemble consensus
        if all_maxes_c:
            consensus_c = sum(all_maxes_c) / len(all_maxes_c)
            print(f"\n  {'Consensus':<14} {temp_str(consensus_c):>10}   (avg of {len(all_maxes_c)} models)")
            print()
        else:
            print("\n  No forecast data available.", file=sys.stderr)
            sys.exit(1)

        # Step 3: Polymarket comparison
        if not args.no_polymarket:
            print(f"  🎯 Polymarket weather markets for '{city_display.split(',')[0]}':")
            markets = await fetch_polymarket_weather_markets(session, city_display, target_date)
            if not markets:
                print("  No matching markets found on Polymarket.\n")
                print("  Tips: Markets may not exist for this city/date, or the city name")
                print("  may need to match Polymarket's naming (e.g. 'NYC', 'New York City').")
            else:
                print(f"  Found {len(markets)} market(s):\n")
                for m in markets[:10]:
                    bucket = _parse_temp_from_question(m["question"])
                    yes_price = m["yes_price"]

                    edge_str = ""
                    if bucket and all_maxes_c:
                        bucket_min_c, bucket_max_c = bucket if isinstance(bucket, tuple) else (None, None)
                        model_prob = 0.0
                        probs = []
                        for max_c in all_maxes_c:
                            probs.append(_bucket_probability(max_c, bucket_min_c, bucket_max_c))
                        model_prob = sum(probs) / len(probs)
                        edge = model_prob - yes_price
                        edge_str = (
                            f"  edge={edge*100:+.1f}pp "
                            f"(model={model_prob*100:.0f}% vs mkt={yes_price*100:.0f}%)"
                        )

                    print(f"  Q: {m['question']}")
                    print(f"     YES price: {yes_price*100:.1f}%{edge_str}")
                    print(f"     URL: {m['url']}")
                    print()


if __name__ == "__main__":
    asyncio.run(main())
