"""
Polymarket Weather Checker — Streamlit version
Forecasts: ECMWF IFS + GFS-GraphCast + GFS  vs  Polymarket temperature markets
Current conditions: Open-Meteo live data (temp, wind, humidity + timestamp)
"""
import os
import re
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st


# ── Persistent station database (Polymarket-verified) ────────────────────────
STATIONS_DB_PATH = os.path.join(os.path.dirname(__file__), "polymarket_stations.json")


@st.cache_resource
def load_stations_db() -> dict:
    """Load the verified Polymarket stations DB committed to the repo."""
    try:
        with open(STATIONS_DB_PATH) as f:
            data = json.load(f)
        return data.get("stations", {})
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def stations_by_alias() -> dict:
    """Build a lookup: alias → station_info from the JSON DB."""
    db = load_stations_db()
    out = {}
    for icao, info in db.items():
        for alias in info.get("polymarket_aliases", []):
            out[alias.lower()] = {
                "icao": icao,
                "name": f"{info.get('city','')} — {info.get('name','')}",
            }
        out[icao.lower()] = {
            "icao": icao,
            "name": f"{info.get('city','')} — {info.get('name','')}",
        }
    return out

# ── Constants ─────────────────────────────────────────────────────────────────

GEOCODE_URL  = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
POLYMARKET   = "https://gamma-api.polymarket.com"
AVWX_METAR   = "https://aviationweather.gov/api/data/metar"
AVWX_STATION = "https://aviationweather.gov/api/data/stationinfo"
SIGMA        = 2.5   # °C day-ahead forecast uncertainty (fallback only)

# ── Polymarket Weather Stations (METAR ASOS) ──────────────────────────────────
# Polymarket weather markets resolve via specific airport METAR stations.
# Forecasts and current observations MUST be from these exact stations.
# Keys are normalized lowercase aliases the user might type.
POLYMARKET_STATIONS = {
    # New York — Polymarket uses Central Park (KNYC) for "NYC" markets
    "nyc":            {"icao": "KNYC", "name": "New York — Central Park"},
    "new york":       {"icao": "KNYC", "name": "New York — Central Park"},
    "new york city":  {"icao": "KNYC", "name": "New York — Central Park"},
    "central park":   {"icao": "KNYC", "name": "New York — Central Park"},
    "knyc":           {"icao": "KNYC", "name": "New York — Central Park"},
    # Los Angeles — KLAX
    "lax":            {"icao": "KLAX", "name": "Los Angeles — LAX"},
    "los angeles":    {"icao": "KLAX", "name": "Los Angeles — LAX"},
    "klax":           {"icao": "KLAX", "name": "Los Angeles — LAX"},
    # Miami
    "mia":            {"icao": "KMIA", "name": "Miami — MIA"},
    "miami":          {"icao": "KMIA", "name": "Miami — MIA"},
    "kmia":           {"icao": "KMIA", "name": "Miami — MIA"},
    # Denver
    "den":            {"icao": "KDEN", "name": "Denver — DEN"},
    "denver":         {"icao": "KDEN", "name": "Denver — DEN"},
    "kden":           {"icao": "KDEN", "name": "Denver — DEN"},
    # Chicago
    "ord":            {"icao": "KORD", "name": "Chicago — O'Hare"},
    "chicago":        {"icao": "KORD", "name": "Chicago — O'Hare"},
    "kord":           {"icao": "KORD", "name": "Chicago — O'Hare"},
    # Austin
    "aus":            {"icao": "KAUS", "name": "Austin-Bergstrom"},
    "austin":         {"icao": "KAUS", "name": "Austin-Bergstrom"},
    "kaus":           {"icao": "KAUS", "name": "Austin-Bergstrom"},
    # Phoenix
    "phx":            {"icao": "KPHX", "name": "Phoenix — Sky Harbor"},
    "phoenix":        {"icao": "KPHX", "name": "Phoenix — Sky Harbor"},
    "kphx":           {"icao": "KPHX", "name": "Phoenix — Sky Harbor"},
    # Houston (Polymarket usually uses KIAH; sometimes KHOU)
    "iah":            {"icao": "KIAH", "name": "Houston — Intercontinental"},
    "houston":        {"icao": "KIAH", "name": "Houston — Intercontinental"},
    "kiah":           {"icao": "KIAH", "name": "Houston — Intercontinental"},
    # Dallas
    "dfw":            {"icao": "KDFW", "name": "Dallas/Fort Worth"},
    "dallas":         {"icao": "KDFW", "name": "Dallas/Fort Worth"},
    "kdfw":           {"icao": "KDFW", "name": "Dallas/Fort Worth"},
    # Philadelphia
    "phl":            {"icao": "KPHL", "name": "Philadelphia — PHL"},
    "philadelphia":   {"icao": "KPHL", "name": "Philadelphia — PHL"},
    "kphl":           {"icao": "KPHL", "name": "Philadelphia — PHL"},
    # Boston
    "bos":            {"icao": "KBOS", "name": "Boston — Logan"},
    "boston":         {"icao": "KBOS", "name": "Boston — Logan"},
    "kbos":           {"icao": "KBOS", "name": "Boston — Logan"},
    # Atlanta
    "atl":            {"icao": "KATL", "name": "Atlanta — Hartsfield"},
    "atlanta":        {"icao": "KATL", "name": "Atlanta — Hartsfield"},
    "katl":           {"icao": "KATL", "name": "Atlanta — Hartsfield"},
    # Seattle
    "sea":            {"icao": "KSEA", "name": "Seattle — SeaTac"},
    "seattle":        {"icao": "KSEA", "name": "Seattle — SeaTac"},
    "ksea":           {"icao": "KSEA", "name": "Seattle — SeaTac"},
    # San Francisco
    "sfo":            {"icao": "KSFO", "name": "San Francisco — SFO"},
    "san francisco":  {"icao": "KSFO", "name": "San Francisco — SFO"},
    "ksfo":           {"icao": "KSFO", "name": "San Francisco — SFO"},
    # Las Vegas
    "las":            {"icao": "KLAS", "name": "Las Vegas — Harry Reid"},
    "las vegas":      {"icao": "KLAS", "name": "Las Vegas — Harry Reid"},
    "vegas":          {"icao": "KLAS", "name": "Las Vegas — Harry Reid"},
    "klas":           {"icao": "KLAS", "name": "Las Vegas — Harry Reid"},
    # Washington DC
    "dca":            {"icao": "KDCA", "name": "Washington — Reagan National"},
    "washington":     {"icao": "KDCA", "name": "Washington — Reagan National"},
    "dc":             {"icao": "KDCA", "name": "Washington — Reagan National"},
    "kdca":           {"icao": "KDCA", "name": "Washington — Reagan National"},
    # Portland
    "pdx":            {"icao": "KPDX", "name": "Portland — PDX"},
    "portland":       {"icao": "KPDX", "name": "Portland — PDX"},
    "kpdx":           {"icao": "KPDX", "name": "Portland — PDX"},
    # Minneapolis
    "msp":            {"icao": "KMSP", "name": "Minneapolis-St Paul"},
    "minneapolis":    {"icao": "KMSP", "name": "Minneapolis-St Paul"},
    "kmsp":           {"icao": "KMSP", "name": "Minneapolis-St Paul"},
    # Detroit
    "dtw":            {"icao": "KDTW", "name": "Detroit Metropolitan"},
    "detroit":        {"icao": "KDTW", "name": "Detroit Metropolitan"},
    "kdtw":           {"icao": "KDTW", "name": "Detroit Metropolitan"},
    # San Diego
    "san":            {"icao": "KSAN", "name": "San Diego — SAN"},
    "san diego":      {"icao": "KSAN", "name": "San Diego — SAN"},
    "ksan":           {"icao": "KSAN", "name": "San Diego — SAN"},
    # Tampa
    "tpa":            {"icao": "KTPA", "name": "Tampa International"},
    "tampa":          {"icao": "KTPA", "name": "Tampa International"},
    "ktpa":           {"icao": "KTPA", "name": "Tampa International"},
    # Orlando
    "mco":            {"icao": "KMCO", "name": "Orlando International"},
    "orlando":        {"icao": "KMCO", "name": "Orlando International"},
    "kmco":           {"icao": "KMCO", "name": "Orlando International"},
}

ENSEMBLE_MODELS = [
    {"id": "ecmwf_ifs025",  "label": "ECMWF ENS",   "expected": 51},
    {"id": "gfs025",        "label": "GEFS",        "expected": 31},
    {"id": "icon_seamless", "label": "ICON-EPS",    "expected": 40},
    {"id": "gem_global",    "label": "GEPS",        "expected": 21},
]

MODELS = [
    {
        "key": "ecmwf",
        "id": "ecmwf_ifs025",
        "label": "ECMWF IFS",
        "desc": "European Centre — physics model",
        "color": "#4e9af1",
    },
    {
        "key": "graphcast",
        "id": "gfs_graphcast025",
        "label": "GFS-GraphCast",
        "desc": "NOAA × Google DeepMind AI (10-day)",
        "fallback": "ecmwf_aifs025_single",
        "fallback_label": "ECMWF AIFS",
        "fallback_desc": "ECMWF AI model — graph neural network",
        "color": "#f1c40f",
    },
    {
        "key": "gfs",
        "id": "gfs_seamless",
        "label": "GFS",
        "desc": "NOAA Global Forecast System — physics model",
        "color": "#e67e22",
    },
]

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

WEATHER_KW = [
    "temperature", "high temp", "degrees", "°",
    "fahrenheit", "celsius", "weather", "hottest", "warmest", "exceed",
]

COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
           "S","SSW","SW","WSW","W","WNW","NW","NNW"]

# ── Math ──────────────────────────────────────────────────────────────────────

def normal_cdf(x: float, mu: float, sigma: float) -> float:
    from math import erf as _erf, sqrt
    return 0.5 * (1 + _erf((x - mu) / (sigma * sqrt(2))))


def bucket_probability(forecast_c, min_c, max_c) -> float:
    lo = normal_cdf(min_c, forecast_c, SIGMA) if min_c is not None else 0.0
    hi = normal_cdf(max_c, forecast_c, SIGMA) if max_c is not None else 1.0
    return max(0.01, min(0.99, hi - lo))

# ── Airport / METAR ───────────────────────────────────────────────────────────

@st.cache_data(ttl=86400)   # station coords don't change
def fetch_station_info(icao: str) -> dict:
    """Get airport coordinates and metadata from NOAA aviationweather.gov."""
    r = requests.get(AVWX_STATION,
                     params={"ids": icao, "format": "json"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"Station {icao} not found in NOAA database")
    s = data[0] if isinstance(data, list) else data
    lat = s.get("latitude") if s.get("latitude") is not None else s.get("lat")
    lon = s.get("longitude") if s.get("longitude") is not None else s.get("lon")
    if lat is None or lon is None:
        raise ValueError(f"Station {icao} has no coordinates")
    return {
        "icao":  s.get("icaoId", icao),
        "iata":  s.get("iataId", ""),
        "lat":   float(lat),
        "lon":   float(lon),
        "name":  s.get("site", icao),
        "state": s.get("state", ""),
        "country": s.get("country", ""),
        "elev_m": s.get("elev"),
    }


@st.cache_data(ttl=300)   # METAR updates ~hourly; refresh every 5 min
def fetch_metar(icao: str) -> dict:
    """Latest METAR observation from NOAA. Returns parsed + raw."""
    r = requests.get(AVWX_METAR,
                     params={"ids": icao, "format": "json", "hours": 3},
                     timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"No METAR available for {icao} in last 3 hours")
    obs = data[0] if isinstance(data, list) else data   # most recent
    return {
        "icao":         obs.get("icaoId", icao),
        "raw":          obs.get("rawOb", ""),
        "obs_time":     obs.get("reportTime") or obs.get("obsTime", ""),
        "temp_c":       obs.get("temp"),
        "dewp_c":       obs.get("dewp"),
        "wind_dir":     obs.get("wdir"),
        "wind_kt":      obs.get("wspd"),
        "wind_gust_kt": obs.get("wgst"),
        "alt_inhg":     obs.get("altim"),
        "vis_sm":       obs.get("visib"),
        "name":         obs.get("name", ""),
    }


def kt_to_kmh(kt):
    if kt is None:
        return None
    try:
        return float(kt) * 1.852
    except (TypeError, ValueError):
        return None


def resolve_station(user_input: str) -> dict | None:
    """
    Recognise Polymarket airport codes / names and return station info.
    Lookup priority: 1) JSON DB (verified)  2) hardcoded fallback  3) raw ICAO.
    Returns dict with {icao, name, lat, lon} or None to fall through to city geocoding.
    """
    if not user_input:
        return None
    s = user_input.strip().lower()

    # 1. Verified Polymarket stations DB (loaded from JSON file)
    db_aliases = stations_by_alias()
    if s in db_aliases:
        info = db_aliases[s]
        try:
            station = fetch_station_info(info["icao"])
            return {**station, "display_name": info["name"], "icao": info["icao"],
                    "verified": True}
        except Exception:
            return None

    # 2. Hardcoded fallback (best-guess city → station mappings)
    if s in POLYMARKET_STATIONS:
        info = POLYMARKET_STATIONS[s]
        try:
            station = fetch_station_info(info["icao"])
            return {**station, "display_name": info["name"], "icao": info["icao"],
                    "verified": False}
        except Exception:
            return None

    # 3. Raw ICAO code (KLAX, EGLL, etc.)
    if re.match(r'^[A-Z]{4}$', user_input.strip().upper()):
        icao = user_input.strip().upper()
        try:
            station = fetch_station_info(icao)
            return {**station, "display_name": f"{icao} — {station.get('name', icao)}",
                    "icao": icao, "verified": False}
        except Exception:
            return None

    return None


# ── Polymarket-wide market discovery ──────────────────────────────────────────

@st.cache_data(ttl=600)
def discover_all_weather_markets() -> list:
    """
    Query the /markets endpoint directly — it returns market objects with
    question + description, unlike /events search which returns empty markets[].
    Falls back to per-slug event fetch for any event slugs seen in search.
    """
    all_markets: list[dict] = []
    seen_ids: set[str] = set()

    # Primary: /markets endpoint (returns full market objects with descriptions)
    queries = ["temperature", "highest temperature", "degrees fahrenheit",
               "hottest", "warmest", "high temp"]
    for q in queries:
        try:
            r = requests.get(f"{POLYMARKET}/markets", params={
                "q":     q,
                "active": "true",
                "limit": 100,
            }, timeout=20)
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            for m in items:
                mid = str(m.get("id") or m.get("conditionId") or "")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_markets.append(m)
        except Exception:
            pass

    # Fallback: collect slugs from /events search, fetch each individually
    if not all_markets:
        slugs: set[str] = set()
        for q in queries[:3]:
            try:
                r = requests.get(f"{POLYMARKET}/events", params={
                    "q": q, "active": "true", "limit": 100}, timeout=20)
                r.raise_for_status()
                data = r.json()
                for e in (data if isinstance(data, list) else data.get("data", [])):
                    if e.get("slug"):
                        slugs.add(e["slug"])
            except Exception:
                pass
        for slug in list(slugs)[:60]:
            try:
                r = requests.get(f"{POLYMARKET}/events", params={"slug": slug, "limit": 1}, timeout=10)
                r.raise_for_status()
                data = r.json()
                events = data if isinstance(data, list) else data.get("data", [])
                for event in events:
                    for mkt in event.get("markets", []):
                        mid = str(mkt.get("id") or mkt.get("conditionId") or "")
                        if mid and mid not in seen_ids:
                            seen_ids.add(mid)
                            mkt.setdefault("eventSlug", slug)
                            all_markets.append(mkt)
            except Exception:
                pass

    return all_markets


# ── Geocoding ─────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def geocode_city(city_input: str) -> dict:
    r = requests.get(GEOCODE_URL, params={"name": city_input, "count": 1,
                                          "language": "en", "format": "json"}, timeout=10)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError(f"City not found: '{city_input}'")
    loc = results[0]
    return {
        "lat": loc["latitude"],
        "lon": loc["longitude"],
        "name": loc["name"],
        "display_name": ", ".join(filter(None, [loc["name"], loc.get("admin1"), loc.get("country")])),
        "timezone": loc.get("timezone", "auto"),
    }

# ── Current Observation ───────────────────────────────────────────────────────

@st.cache_data(ttl=600)   # refresh every 10 min
def fetch_current_obs(lat: float, lon: float) -> dict:
    """
    Returns current temp, feels-like, wind, humidity and the observation timestamp.
    Source: Open-Meteo /v1/forecast with current= parameter (updates ~hourly).
    NOTE: Open-Meteo current weather is the closest free equivalent to Weather Underground
    live observations. Polymarket resolves via Wunderground airport stations; the readings
    are virtually identical since both source METAR ASOS airport data.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join([
            "temperature_2m",
            "apparent_temperature",
            "relative_humidity_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "precipitation",
            "weather_code",
        ]),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }
    r = requests.get(FORECAST_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    cur  = data.get("current", {})
    return {
        "temp_c":       cur.get("temperature_2m"),
        "feels_c":      cur.get("apparent_temperature"),
        "humidity":     cur.get("relative_humidity_2m"),
        "wind_kmh":     cur.get("wind_speed_10m"),
        "wind_deg":     cur.get("wind_direction_10m"),
        "gusts_kmh":    cur.get("wind_gusts_10m"),
        "precip_mm":    cur.get("precipitation"),
        "obs_time":     cur.get("time", ""),          # local time from API
        "timezone":     data.get("timezone", "UTC"),
    }

# ── Forecast ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=1800)   # 30-min cache
def fetch_forecast(lat: float, lon: float, date_str: str, model_id: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": "celsius",
        "timezone": "auto",
        "models": model_id,
        "start_date": date_str,
        "end_date": date_str,
    }
    r = requests.get(FORECAST_URL, params=params, timeout=15)
    if not r.ok:
        raise ValueError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("error"):
        raise ValueError(data.get("reason", "Unknown API error"))
    daily = data.get("daily", {})
    times = daily.get("time", [])
    if date_str not in times:
        raise ValueError(f"Date {date_str} outside forecast window")
    idx   = times.index(date_str)
    max_c = (daily.get("temperature_2m_max") or [None])[idx]
    min_c = (daily.get("temperature_2m_min") or [None])[idx]
    if max_c is None:
        raise ValueError("No temperature data returned")
    return {
        "max_c": round(max_c, 1),
        "min_c": round(min_c, 1) if min_c is not None else None,
        "max_f": round(max_c * 9/5 + 32, 1),
        "min_f": round(min_c * 9/5 + 32, 1) if min_c is not None else None,
    }


# ── Ensemble (probabilistic) forecast ─────────────────────────────────────────

@st.cache_data(ttl=1800)
def fetch_ensemble_members(lat: float, lon: float, date_str: str, model_id: str) -> list:
    """
    Returns list of daily-max temperatures (°C), one per ensemble member,
    by taking the hourly max of each member across the target date's local hours.
    Each ensemble member is a perturbed run of the same model, so the spread
    of these numbers IS the actual probability distribution.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m",
        "timezone": "auto",
        "models": model_id,
        "start_date": date_str,
        "end_date": date_str,
    }
    r = requests.get(ENSEMBLE_URL, params=params, timeout=20)
    if not r.ok:
        raise ValueError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("error"):
        raise ValueError(data.get("reason", "Unknown ensemble API error"))

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    if not times:
        raise ValueError("No ensemble hourly data returned")

    target_idx = [i for i, t in enumerate(times) if t.startswith(date_str)]
    if not target_idx:
        raise ValueError(f"Date {date_str} not in ensemble window")

    member_cols = [k for k in hourly.keys() if re.match(r'^temperature_2m_member\d+$', k)]
    if not member_cols and "temperature_2m" in hourly:
        member_cols = ["temperature_2m"]
    if not member_cols:
        raise ValueError("No ensemble members in response")

    maxes = []
    for col in member_cols:
        vals = [hourly[col][i] for i in target_idx if hourly[col][i] is not None]
        if vals:
            maxes.append(max(vals))
    if not maxes:
        raise ValueError("All ensemble members were null")
    return maxes


def fetch_super_ensemble(lat: float, lon: float, date_str: str) -> tuple:
    """Fetch members from every ensemble model; pool them into a super-ensemble."""
    by_model = {}
    pooled   = []
    for model in ENSEMBLE_MODELS:
        try:
            members = fetch_ensemble_members(lat, lon, date_str, model["id"])
            by_model[model["label"]] = {
                "members": members,
                "mean":    sum(members) / len(members),
                "std":     float(np.std(members, ddof=1)) if len(members) > 1 else 0.0,
                "n":       len(members),
                "error":   None,
            }
            pooled.extend(members)
        except Exception as e:
            by_model[model["label"]] = {"members": [], "mean": None, "std": None,
                                        "n": 0, "error": str(e)}
    return pooled, by_model


def empirical_bucket_prob(members: list, min_c, max_c) -> float | None:
    if not members:
        return None
    n = len(members)
    count = sum(
        1 for m in members
        if (min_c is None or m >= min_c) and (max_c is None or m <= max_c)
    )
    return count / n


def empirical_quantiles(members: list) -> dict:
    if not members:
        return {}
    s = sorted(members)
    n = len(s)
    def q(p):
        idx = max(0, min(n - 1, int(p * n)))
        return s[idx]
    return {
        "p10":  q(0.10),
        "p25":  q(0.25),
        "p50":  q(0.50),
        "p75":  q(0.75),
        "p90":  q(0.90),
        "min":  s[0],
        "max":  s[-1],
        "mean": sum(s) / n,
        "std":  float(np.std(s, ddof=1)) if n > 1 else 0.0,
        "n":    n,
    }


def degree_histogram_f(members: list) -> pd.DataFrame:
    """Probability mass per integer °F bin — exactly what the user asked for."""
    if not members:
        return pd.DataFrame()
    members_f = [m * 9/5 + 32 for m in members]
    rounded   = [round(f) for f in members_f]
    n         = len(members_f)
    counts    = {}
    for r in rounded:
        counts[r] = counts.get(r, 0) + 1
    lo, hi = min(counts), max(counts)
    rows = []
    for t in range(lo, hi + 1):
        c = counts.get(t, 0)
        rows.append({"Max Temp (°F)": t, "Probability": c / n, "Members": c})
    return pd.DataFrame(rows)


def fetch_model_with_fallback(lat, lon, date_str, model) -> dict:
    attempts = [(model["id"], model["label"], model["desc"])]
    if "fallback" in model:
        attempts.append((model["fallback"], model.get("fallback_label", ""), model.get("fallback_desc", "")))

    errors = []
    for mid, mlabel, mdesc in attempts:
        try:
            result = fetch_forecast(lat, lon, date_str, mid)
            return {**result, "label": mlabel, "desc": mdesc, "used_id": mid, "error": None}
        except Exception as e:
            errors.append(f"[{mid}] {e}")

    return {"max_c": None, "min_c": None, "max_f": None, "min_f": None,
            "label": model["label"], "desc": model["desc"], "used_id": model["id"],
            "error": " | ".join(errors)}

# ── Polymarket helpers ────────────────────────────────────────────────────────

def slug_from_url(url: str):
    if not url:
        return None
    m = re.search(r'polymarket\.com/event/([^/?#\s]+)', url.strip())
    return m.group(1) if m else None


def _months_re():
    return "|".join(MONTHS)


def parse_date_from_question(q: str):
    if not q:
        return None
    m = re.search(rf'(?:on|for)\s+({_months_re()})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?',
                  q.lower())
    if not m:
        return None
    month = MONTHS.get(m.group(1))
    if not month:
        return None
    day  = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else None
    if not year:
        today = date.today()
        year  = today.year
        cand  = date(year, month, day)
        if cand < today:
            year += 1
    return f"{year}-{month:02d}-{day:02d}"


def parse_date_from_slug(slug: str):
    if not slug:
        return None
    m = re.search(rf'({_months_re()})-(\d{{1,2}})(?:st|nd|rd|th)?-(\d{{4}})', slug.lower())
    if not m:
        return None
    month = MONTHS.get(m.group(1))
    if not month:
        return None
    day, year = int(m.group(2)), int(m.group(3))
    if day < 1 or day > 31 or year < 2020:
        return None
    return f"{year}-{month:02d}-{day:02d}"


def parse_city_from_question(q: str):
    if not q:
        return None
    m = re.search(r'\bin\s+([A-Z][A-Za-z\s\'.-]+?)(?:\s+(?:on|for|exceed|above|below|be\s|reach|temperature\b|(?=\d)))', q)
    if m:
        return m.group(1).strip()
    m = re.search(r'\bin\s+((?:[A-Z][a-z]+\s?){1,3})', q)
    return m.group(1).strip() if m else None


def parse_city_from_slug(slug: str):
    if not slug:
        return None
    stop = {'high','low','temperature','temp','weather','degrees','fahrenheit','celsius',
            'above','below','exceed','will','the','be','for','on','highest','hottest'} | set(MONTHS)
    words = slug.split('-')
    city  = []
    for w in words:
        if re.match(r'^\d+$', w) or w in stop:
            break
        city.append(w.capitalize())
    return ' '.join(city) if city else None


def parse_temp_bucket(question: str):
    if not question:
        return None
    q    = question.lower()
    is_f = bool(re.search(r'°f|fahrenheit', q)) or (
           bool(re.search(r'\bdegrees?\b', q)) and not re.search(r'celsius|°c', q))
    def to_c(v):
        return round((v - 32) * 5/9, 2) if is_f else round(v, 2)

    m = re.search(r'\bno\s+more\s+than\s+(\d+\.?\d*)', q)
    if m:
        return {"min_c": None, "max_c": to_c(float(m.group(1)))}
    m = re.search(r'\bbetween\s+(\d+\.?\d*)\s+and\s+(\d+\.?\d*)', q)
    if m:
        return {"min_c": to_c(float(m.group(1))), "max_c": to_c(float(m.group(2)))}
    m = re.search(r'(?:above|exceed[s]?|over|higher than|at least|more than|reach)\s+(\d+\.?\d*)', q)
    if m:
        return {"min_c": to_c(float(m.group(1))), "max_c": None}
    m = re.search(r'(?:below|under|less than|not exceed)\s+(\d+\.?\d*)', q)
    if m:
        return {"min_c": None, "max_c": to_c(float(m.group(1)))}
    return None


@st.cache_data(ttl=300)
def fetch_poly_event(slug: str) -> dict:
    r = requests.get(f"{POLYMARKET}/events", params={"slug": slug, "limit": 1}, timeout=10)
    r.raise_for_status()
    data = r.json()
    events = data if isinstance(data, list) else data.get("data", [])
    if not events:
        raise ValueError(f"No event found for slug: {slug}")
    return events[0]


@st.cache_data(ttl=300)
def search_poly_city(city_name: str) -> list:
    r = requests.get(f"{POLYMARKET}/events",
                     params={"q": city_name, "active": "true", "closed": "false", "limit": 50},
                     timeout=10)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("data", [])


def extract_resolution_station(description: str) -> dict | None:
    """
    Polymarket weather markets state the EXACT resolution station in the description.
    Example:
      "...recorded at the Buckley Space Force Base Station..."
      "...available here: https://www.wunderground.com/history/daily/us/co/aurora/KBKF"
    The Wunderground URL's last path segment is the ICAO. Returns:
      {icao, station_name, wunderground_url, raw_excerpt} or None
    """
    if not description:
        return None

    # Wunderground URL — the last path segment is the station ID (ICAO for ASOS)
    url_m = re.search(
        r'(https?://(?:www\.)?wunderground\.com/[\w/\-]+/([A-Z0-9]{4,12}))(?=[/\s\)\.\,]|$)',
        description, re.IGNORECASE)

    icao = None
    wu_url = None
    if url_m:
        wu_url = url_m.group(1)
        candidate = url_m.group(2).upper()
        # Only accept 4-letter ICAO format (Polymarket-resolution-grade)
        if re.match(r'^[A-Z]{4}$', candidate):
            icao = candidate

    # Station name in prose: "recorded at the Buckley Space Force Base Station"
    name_m = re.search(
        r'recorded at(?: the)? ([A-Z][\w\s\-\.\']+?)(?:\s+[Ss]tation|\s+in degrees)',
        description)
    station_name = name_m.group(1).strip() if name_m else ""

    if not icao:
        return None

    return {
        "icao":             icao,
        "station_name":     station_name,
        "wunderground_url": wu_url or "",
    }


def _parse_prices(market: dict) -> tuple:
    """Return (yes_price, no_price) from outcomePrices or tokens."""
    try:
        raw    = market.get("outcomePrices")
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, list) and len(parsed) >= 2:
            return float(parsed[0]), float(parsed[1])
        if isinstance(parsed, list) and len(parsed) == 1:
            y = float(parsed[0])
            return y, round(1 - y, 4)
    except Exception:
        pass
    # Tokens fallback (CLOB structure)
    tokens = market.get("tokens") or []
    if isinstance(tokens, list) and len(tokens) >= 2:
        try:
            prices = {t.get("outcome", "").lower(): float(t.get("price", 0))
                      for t in tokens if t.get("price") is not None}
            y = prices.get("yes", prices.get("1", 0.5))
            n = prices.get("no", prices.get("0", round(1 - y, 4)))
            return y, n
        except Exception:
            pass
    return 0.5, 0.5


def _make_market_entry(question: str, description: str, yes_price: float,
                       no_price: float, url: str, end_date: str) -> dict:
    resolution = extract_resolution_station(description)
    return {
        "question":    question,
        "description": description,
        "resolution":  resolution,
        "yes_price":   yes_price,
        "no_price":    no_price,
        "url":         url,
        "bucket":      parse_temp_bucket(question),
        "end_date":    end_date,
    }


def parse_markets(events_or_markets: list, city_filter: str | None = None) -> list:
    """
    Accepts either:
     - list of EVENT objects (each has a 'markets' sub-list), OR
     - list of raw MARKET objects (from /markets endpoint — no sub-list).
    """
    results    = []
    fltr_lower = city_filter.lower() if city_filter else None

    def _accept(q: str) -> bool:
        ql = q.lower()
        if fltr_lower and fltr_lower not in ql:
            return False
        return any(kw in ql for kw in WEATHER_KW)

    for item in events_or_markets:
        sub_markets = item.get("markets")

        # ── Raw market object (from /markets endpoint) ───────────────────────
        if sub_markets is None:
            q = item.get("question", "")
            if not q or not _accept(q):
                continue
            desc = item.get("description", "") or ""
            # Prefer event slug for URL (groupSlug / slug field on market)
            slug = (item.get("groupSlug")
                    or item.get("eventSlug")
                    or item.get("slug")
                    or "")
            yes_p, no_p = _parse_prices(item)
            results.append(_make_market_entry(
                q, desc, yes_p, no_p,
                f"https://polymarket.com/event/{slug}" if slug else "",
                item.get("endDate", ""),
            ))
            continue

        # ── Event object containing sub-markets ─────────────────────────────
        slug       = item.get("slug", "")
        event_desc = item.get("description", "") or ""
        for mkt in (sub_markets or []):
            q = mkt.get("question", "")
            if not q or not _accept(q):
                continue
            desc = mkt.get("description", "") or event_desc
            yes_p, no_p = _parse_prices(mkt)
            results.append(_make_market_entry(
                q, desc, yes_p, no_p,
                f"https://polymarket.com/event/{slug}",
                mkt.get("endDate", item.get("endDate", "")),
            ))

    return results


def enrich_markets(markets: list, model_results: list,
                   ensemble_members: list | None = None,
                   ensemble_by_model: dict | None = None) -> list:
    """
    Compute probability for each Polymarket bucket.
    Priority:  empirical ensemble probability  >  normal approx around deterministic.
    """
    valid_det = [r for r in model_results if r.get("max_c") is not None]
    det_max   = [r["max_c"] for r in valid_det]
    enriched  = []

    for m in markets:
        bucket = m.get("bucket")
        if not bucket:
            enriched.append({**m, "model_prob": None, "edge": None,
                             "per_model": [], "method": "no-bucket"})
            continue

        if ensemble_members:
            model_prob = empirical_bucket_prob(
                ensemble_members, bucket.get("min_c"), bucket.get("max_c"))
            method     = f"empirical ({len(ensemble_members)} ensemble members)"
            per_model  = []
            if ensemble_by_model:
                for label, info in ensemble_by_model.items():
                    if info.get("members"):
                        p = empirical_bucket_prob(
                            info["members"], bucket.get("min_c"), bucket.get("max_c"))
                        if p is not None:
                            per_model.append((label, p))
        elif det_max:
            probs      = [bucket_probability(fc, bucket.get("min_c"), bucket.get("max_c"))
                          for fc in det_max]
            model_prob = sum(probs) / len(probs)
            method     = f"normal approx (σ={SIGMA}°C)"
            per_model  = list(zip([r["label"] for r in valid_det], probs))
        else:
            enriched.append({**m, "model_prob": None, "edge": None,
                             "per_model": [], "method": "no-data"})
            continue

        enriched.append({
            **m,
            "model_prob": model_prob,
            "edge":       (model_prob - m["yes_price"]) if model_prob is not None else None,
            "per_model":  per_model,
            "method":     method,
        })
    return enriched

# ── UI helpers ────────────────────────────────────────────────────────────────

def c_to_f(c):
    return round(c * 9/5 + 32, 1) if c is not None else None


def deg_to_compass(deg):
    if deg is None:
        return "—"
    return COMPASS[round(deg / 22.5) % 16]


def fmt_edge(edge):
    if edge is None:
        return "—"
    pp = edge * 100
    if pp >= 10:   return f"🟢 +{pp:.1f}pp  BUY YES"
    if pp >= 5:    return f"🟡 +{pp:.1f}pp"
    if pp <= -10:  return f"🔴 {pp:.1f}pp  BUY NO"
    if pp <= -5:   return f"🟠 {pp:.1f}pp"
    return f"{pp:+.1f}pp"


def fmt_bucket(bucket):
    if not bucket:
        return "unrecognised format"
    lo, hi = bucket.get("min_c"), bucket.get("max_c")
    def fv(c):
        return f"{c:.1f}°C ({c*9/5+32:.1f}°F)"
    if lo is not None and hi is not None:
        return f"{fv(lo)} – {fv(hi)}"
    if lo is not None:
        return f"above {fv(lo)}"
    if hi is not None:
        return f"below {fv(hi)}"
    return "—"


def render_metar(metar: dict, station_name: str):
    """Live METAR observation — same NOAA ASOS source that Wunderground uses."""
    tc   = metar.get("temp_c")
    dp   = metar.get("dewp_c")
    wd   = metar.get("wind_dir")
    wkt  = metar.get("wind_kt")
    gkt  = metar.get("wind_gust_kt")
    raw  = metar.get("raw", "")
    ts   = metar.get("obs_time", "")
    alt  = metar.get("alt_inhg")
    vis  = metar.get("vis_sm")

    def _tc(c):
        if c is None:
            return "—"
        return f"{c:.1f}°C  ({c*9/5+32:.1f}°F)"

    # Humidity from temp + dewpoint (Magnus formula)
    hum_str = "—"
    if tc is not None and dp is not None:
        try:
            from math import exp as _exp
            rh = 100 * _exp(17.625 * dp / (243.04 + dp)) / _exp(17.625 * tc / (243.04 + tc))
            hum_str = f"{rh:.0f}%"
        except Exception:
            pass

    # Wind
    if wkt is None or wkt == 0:
        wind_label = "Calm"
        wind_help  = "No significant wind"
    else:
        wkmh   = kt_to_kmh(wkt)
        dir_s  = f"{wd}° ({deg_to_compass(wd)})" if wd is not None else "variable"
        wind_label = f"{wkt:.0f} kt  ({wkmh:.0f} km/h)"
        wind_help  = f"Direction: {dir_s}"
        if gkt and float(gkt) > 0:
            wind_label += f"  gusts {float(gkt):.0f} kt ({kt_to_kmh(gkt):.0f} km/h)"

    with st.container(border=True):
        icao = metar.get("icao", "?")
        st.markdown(f"**🛬 Live Station Observation — `{icao}` ({station_name})**")
        st.caption("Source: NOAA ASOS via aviationweather.gov — same raw data Wunderground republishes for Polymarket resolution")

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric(
                "Temperature 🌡",
                f"{tc:.1f}°C" if tc is not None else "—",
                delta=f"{tc*9/5+32:.1f}°F" if tc is not None else None,
                delta_color="off",
                help="Current dry-bulb temperature at the station"
            )
        with c2:
            st.metric(
                "Dew Point 💧",
                f"{dp:.1f}°C" if dp is not None else "—",
                delta=f"{dp*9/5+32:.1f}°F" if dp is not None else None,
                delta_color="off",
                help="Dew point = moisture in air. Closer to temp → more humid"
            )
        with c3:
            st.metric(
                "Humidity",
                hum_str,
                help="Relative humidity calculated from temp + dew point"
            )
        with c4:
            st.metric(
                "Wind 💨",
                wind_label,
                help=wind_help
            )
        with c5:
            st.metric(
                "Visibility",
                f"{float(vis):.0f} sm" if vis else "—",
                help="Statute miles (sm). 10 sm = standard clear visibility"
            )

        if ts:
            # Format timestamp nicely
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts_display = dt.strftime("%d %b %Y  %H:%M UTC")
            except Exception:
                ts_display = ts
            st.info(f"⏱ **Observation time: {ts_display}**  ·  Pressure: {alt:.2f} inHg" if alt else
                    f"⏱ **Observation time: {ts_display}**")

        if raw:
            with st.expander("Raw METAR string (for reference)"):
                st.code(raw, language=None)
                st.caption("METAR format: station  time  wind(dir/speed)kt  vis  clouds  temp/dewpoint  altimeter")


def render_current_obs(obs: dict):
    tc   = obs.get("temp_c")
    tf   = c_to_f(tc)
    fc   = obs.get("feels_c")
    wsp  = obs.get("wind_kmh")
    wdir = obs.get("wind_deg")
    gust = obs.get("gusts_kmh")
    hum  = obs.get("humidity")
    prec = obs.get("precip_mm")
    ts   = obs.get("obs_time", "")
    tz   = obs.get("timezone", "UTC")

    wind_str = "—"
    if wsp is not None:
        wind_str = f"{wsp:.0f} km/h {deg_to_compass(wdir)}"
        if gust and gust > wsp:
            wind_str += f" (gusts {gust:.0f})"

    with st.container(border=True):
        st.markdown("**🌡️ Current Conditions**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Temperature",
                      f"{tc:.1f}°C" if tc is not None else "—",
                      delta=f"{tf:.1f}°F" if tf is not None else None,
                      delta_color="off")
        with c2:
            st.metric("Feels Like", f"{fc:.1f}°C" if fc is not None else "—")
        with c3:
            st.metric("Wind", wind_str)
        with c4:
            st.metric("Humidity", f"{hum}%" if hum is not None else "—")

        # Timestamp — CRITICAL for trading decisions
        if ts:
            st.markdown(
                f"⏱ **Last updated: {ts}** ({tz})"
                f"{'  •  ' + f'Precip: {prec:.1f} mm' if prec else ''}",
                help="Polymarket typically resolves via Weather Underground airport stations, "
                     "which source the same METAR ASOS data as Open-Meteo current observations."
            )


def render_forecast_table(model_results: list, date_str: str):
    valid   = [r for r in model_results if r.get("max_c") is not None]
    con_c   = sum(r["max_c"] for r in valid) / len(valid) if valid else None

    rows = []
    for r in model_results:
        if r.get("error"):
            rows.append({"Model": r["label"], "Description": r["desc"],
                         "Max Temp": "⚠ " + r["error"][:100], "Min Temp": ""})
        else:
            rows.append({
                "Model":       r["label"],
                "Description": r["desc"],
                "Max Temp":    f"{r['max_c']:.1f}°C / {r['max_f']:.1f}°F",
                "Min Temp":    (f"{r['min_c']:.1f}°C / {r['min_f']:.1f}°F"
                                if r.get("min_c") is not None else "—"),
            })

    if con_c is not None:
        con_f = c_to_f(con_c)
        rows.append({
            "Model":       "Consensus",
            "Description": f"avg of {len(valid)} model{'s' if len(valid) != 1 else ''}",
            "Max Temp":    f"{con_c:.1f}°C / {con_f:.1f}°F",
            "Min Temp":    "—",
        })

    st.markdown(f"**📅 Temperature Forecast — {date_str}**")
    st.dataframe(
        pd.DataFrame(rows).set_index("Model"),
        use_container_width=True,
    )
    return con_c


def compute_det_distribution(model_results: list) -> tuple:
    """
    Build synthetic probability distribution from deterministic models.
    Uses a mixture of normals: one Gaussian per model, sigma estimated
    from inter-model spread (σ = max(2.5°C, spread × 1.5)).
    Returns (samples_c, sigma_c, source_label).
    """
    valid = [r for r in model_results if r.get("max_c") is not None]
    if not valid:
        return [], SIGMA, "no data"

    vals = [r["max_c"] for r in valid]
    spread = max(vals) - min(vals) if len(vals) > 1 else 0.0
    sigma  = max(SIGMA, spread * 1.5)

    # 200 samples per model → smooth histogram
    rng     = np.random.default_rng(42)
    samples = np.concatenate([rng.normal(mu, sigma, 200) for mu in vals]).tolist()
    src     = (f"mixture of {len(valid)} deterministic models "
               f"(σ = {sigma:.1f}°C estimated from inter-model spread)")
    return samples, sigma, src


def render_probability_section(date_str: str, model_results: list,
                                pooled_ensemble: list, by_model: dict):
    """
    Always shows a probability distribution.
    Priority: empirical ensemble (if ≥ 10 members) → synthetic from deterministic models.
    """
    from math import sqrt as _sqrt

    st.markdown(f"**🎲 Probability Distribution — Max Temperature on {date_str}**")

    # Choose data source
    if len(pooled_ensemble) >= 10:
        samples   = pooled_ensemble
        valid_ens = [lbl for lbl, info in by_model.items() if info.get("members")]
        source    = f"empirical ensemble ({len(samples)} members: {', '.join(valid_ens)})"
        is_ens    = True
    else:
        samples, sigma_det, source = compute_det_distribution(model_results)
        is_ens = False
        if not samples:
            st.warning("Cannot compute probability distribution — no forecast data available.")
            return None

    if not is_ens and by_model:
        failed = [(lbl, info.get("error","?")) for lbl, info in by_model.items()
                  if not info.get("members")]
        if failed:
            with st.expander("⚠ Ensemble API unavailable — using deterministic fallback"):
                for lbl, err in failed:
                    st.caption(f"**{lbl}**: {err[:150]}")
                st.caption("Probability is estimated from model spread, not from actual ensemble runs.")

    st.caption(f"Source: {source}")

    q = empirical_quantiles(samples)

    # Quantile metrics
    def fmt(c):
        return f"{c:.1f}°C / {c*9/5+32:.1f}°F"

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: st.metric("P10 (cool)",   fmt(q["p10"]))
    with c2: st.metric("P25",          fmt(q["p25"]))
    with c3: st.metric("P50 (median)", fmt(q["p50"]))
    with c4: st.metric("P75",          fmt(q["p75"]))
    with c5: st.metric("P90 (hot)",    fmt(q["p90"]))

    st.caption(
        f"Mean: **{fmt(q['mean'])}**  ·  "
        f"Spread (σ): **{q['std']:.2f}°C**  ·  "
        f"Range shown: {fmt(q['min'])} → {fmt(q['max'])}"
    )

    # Histogram per integer °F
    hist = degree_histogram_f(samples)
    if not hist.empty:
        st.markdown("**Probability per integer °F bin**")
        chart_df = hist.set_index("Max Temp (°F)")[["Probability"]]
        st.bar_chart(chart_df, height=260)

        # Annotated table with cumulative column
        probs = hist["Probability"].tolist()
        table = hist.copy()
        table["Probability %"] = [f"{p*100:.1f}%" for p in probs]
        table["Cumulative ≥"]  = [
            f"{sum(probs[i:])*100:.1f}%"
            for i in range(len(probs))
        ]
        table["Cumulative ≤"]  = [
            f"{sum(probs[:i+1])*100:.1f}%"
            for i in range(len(probs))
        ]
        st.dataframe(
            table.drop(columns=["Probability", "Members"]).set_index("Max Temp (°F)"),
            use_container_width=True,
            height=min(450, 55 + 35 * len(hist)),
        )

    if is_ens:
        with st.expander("Per ensemble model"):
            rows = []
            for label, info in by_model.items():
                if info.get("members"):
                    rows.append({"Model": label, "Members": info["n"],
                                 "Mean": fmt(info["mean"]), "Std": f"{info['std']:.2f}°C"})
                else:
                    rows.append({"Model": label, "Members": 0, "Mean": "—",
                                 "Std": f"⚠ {info.get('error','?')[:70]}"})
            st.dataframe(pd.DataFrame(rows).set_index("Model"), use_container_width=True)

    return samples


def render_markets(markets: list):
    if not markets:
        st.info("No temperature markets found. Try the **By Polymarket URL** tab with a direct link.")
        return

    st.markdown(f"**📊 Polymarket Markets ({len(markets)})**")
    st.caption("Edge = Model probability − Market price  ·  positive → BUY YES  ·  negative → BUY NO")

    sorted_m = sorted(markets, key=lambda m: abs(m.get("edge") or 0), reverse=True)

    for m in sorted_m:
        edge  = m.get("edge")
        mprob = m.get("model_prob")
        yes_p = m.get("yes_price", 0.5)
        no_p  = m.get("no_price", round(1 - yes_p, 4))
        label = fmt_edge(edge)

        with st.expander(f"{label}  —  {m['question'][:90]}{'…' if len(m['question']) > 90 else ''}"):

            # ── Prices row: YES + NO side by side ──────────────────────────
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric(
                    "YES price",
                    f"{yes_p*100:.1f}¢",
                    help="Cost of 1 YES share (pays $1 if market resolves YES)"
                )
            with c2:
                st.metric(
                    "NO price",
                    f"{no_p*100:.1f}¢",
                    help="Cost of 1 NO share (pays $1 if market resolves NO)"
                )
            with c3:
                st.metric(
                    "Model P(YES)",
                    f"{mprob*100:.1f}%" if mprob is not None else "—",
                    help="Probability computed from weather model ensemble"
                )
            with c4:
                no_edge = (1 - mprob - no_p) if mprob is not None else None
                st.metric(
                    "YES edge",
                    f"{edge*100:+.1f}pp" if edge is not None else "—",
                    help="Model P(YES) − YES price. Positive → model says YES is cheap"
                )
            with c5:
                st.metric(
                    "NO edge",
                    f"{no_edge*100:+.1f}pp" if no_edge is not None else "—",
                    help="Model P(NO) − NO price. Positive → model says NO is cheap"
                )

            # ── Resolution station ──────────────────────────────────────────
            if m.get("resolution") and m["resolution"].get("icao"):
                res = m["resolution"]
                st.markdown(
                    f"🛬 **Resolution: `{res['icao']}`"
                    + (f" — {res['station_name']}" if res.get("station_name") else "")
                    + "**"
                )
                if res.get("wunderground_url"):
                    st.caption(f"Wunderground: {res['wunderground_url']}")
            else:
                st.caption("⚠ No resolution station detected in market description")

            if m.get("bucket"):
                st.caption(f"Temperature bucket: {fmt_bucket(m['bucket'])}")
            if m.get("per_model"):
                per = "  ·  ".join(f"{lbl}: {p*100:.1f}%" for lbl, p in m["per_model"])
                st.caption(f"Per model: {per}")
            if m.get("method"):
                st.caption(f"Probability method: {m['method']}")

            if m.get("url"):
                st.link_button("Open on Polymarket ↗", m["url"])

# ── Main analysis flow ────────────────────────────────────────────────────────

def run_analysis(city_input: str, date_str: str, markets_override=None):
    # 1. Try to resolve as Polymarket airport station first
    station = None
    with st.spinner("Resolving station…"):
        station = resolve_station(city_input)

    if station:
        location = {
            "lat":          station["lat"],
            "lon":          station["lon"],
            "name":         station["icao"],
            "display_name": station["display_name"],
            "icao":         station["icao"],
        }
        is_station = True
    else:
        # Fall back to city geocoding
        with st.spinner("Geocoding city…"):
            try:
                location = geocode_city(city_input)
                location["icao"] = None
                is_station = False
            except Exception as e:
                st.error(str(e))
                return None

    with st.container(border=True):
        day_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d, %Y").replace(" 0", " ")
        if is_station:
            st.subheader(f"🛬 {location['display_name']}")
            st.caption(
                f"ICAO: **{location['icao']}**  ·  "
                f"{location['lat']:.4f}°N, {location['lon']:.4f}°E  ·  {day_label}  "
                f"·  ✅ Polymarket-aligned station"
            )
        else:
            st.subheader(f"📍 {location['display_name']}")
            st.caption(
                f"{location['lat']:.4f}°N, {location['lon']:.4f}°E  ·  {day_label}  "
                f"·  ⚠ Not a Polymarket airport — type ICAO (e.g. KLAX) for resolution-grade data"
            )

    # METAR — live airport observation (only when we have an ICAO)
    if is_station:
        with st.spinner(f"Fetching live METAR for {location['icao']}…"):
            try:
                metar = fetch_metar(location["icao"])
                render_metar(metar, location["display_name"])
            except Exception as e:
                st.warning(f"METAR unavailable for {location['icao']}: {e}")

    # Open-Meteo current observation (gridded, supplements METAR)
    with st.spinner("Fetching gridded current conditions…"):
        try:
            obs = fetch_current_obs(location["lat"], location["lon"])
            render_current_obs(obs)
        except Exception as e:
            st.warning(f"Current conditions unavailable: {e}")

    # Forecasts
    with st.spinner("Fetching model forecasts…"):
        model_results = [
            fetch_model_with_fallback(location["lat"], location["lon"], date_str, m)
            for m in MODELS
        ]

    render_forecast_table(model_results, date_str)

    # Probabilistic distribution — always shown; ensemble when available, det-model fallback otherwise
    pooled, by_model = [], {}
    with st.spinner("Fetching ensemble forecasts…"):
        try:
            pooled, by_model = fetch_super_ensemble(location["lat"], location["lon"], date_str)
        except Exception as e:
            st.caption(f"(Ensemble fetch outer error: {e})")

    prob_samples = render_probability_section(date_str, model_results, pooled, by_model)

    # Markets
    if markets_override is not None:
        markets = markets_override
    else:
        markets = []
        with st.spinner("Searching Polymarket…"):
            try:
                events = search_poly_city(location["name"])
                short  = " ".join(location["name"].split()[:2])
                markets = parse_markets(events, short)
            except Exception as e:
                st.warning(f"Polymarket search failed: {e}. Use the URL tab for direct lookup.")

    dist_members = pooled if len(pooled) >= 10 else (prob_samples or [])
    enriched = enrich_markets(markets, model_results, dist_members, by_model)
    render_markets(enriched)
    return model_results

# ── Discover-all flow ─────────────────────────────────────────────────────────

def run_discover_all():
    """Scan Polymarket → extract stations → forecast each → rank by edge."""
    with st.spinner("🔍 Scanning Polymarket Gamma API…"):
        events = discover_all_weather_markets()

    st.success(f"Found **{len(events)}** weather-related events on Polymarket")

    all_markets = parse_markets(events)
    if not all_markets:
        st.warning("No temperature markets found in any event.")
        return

    # Categorise: parseable vs needs-attention
    valid          = []
    no_resolution  = []
    no_bucket      = []
    no_date        = []
    today          = date.today()
    horizon        = today + timedelta(days=16)

    for m in all_markets:
        if not m.get("resolution") or not m["resolution"].get("icao"):
            no_resolution.append(m)
            continue
        if not m.get("bucket"):
            no_bucket.append(m)
            continue
        ds = parse_date_from_question(m["question"]) or parse_date_from_slug(
            m["url"].split("/event/")[-1] if "/event/" in m["url"] else "")
        if not ds:
            no_date.append(m)
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except Exception:
            no_date.append(m)
            continue
        # Skip if already past or beyond forecast window
        if d < today - timedelta(days=1) or d > horizon:
            continue
        m["target_date"] = ds
        valid.append(m)

    unique_stations = sorted(set(m["resolution"]["icao"] for m in valid))
    unique_groups   = set((m["resolution"]["icao"], m["target_date"]) for m in valid)

    c1, c2, c3 = st.columns(3)
    c1.metric("Markets analyzable", len(valid))
    c2.metric("Unique stations",    len(unique_stations))
    c3.metric("Forecast jobs",      len(unique_groups))

    if not valid:
        st.warning("No markets passed all filters (need: resolution station + temp bucket + date in forecast window).")
        return

    # Group markets by (icao, date) to avoid duplicate forecast calls
    groups: dict = {}
    for m in valid:
        groups.setdefault((m["resolution"]["icao"], m["target_date"]), []).append(m)

    progress = st.progress(0.0, text=f"Forecasting {len(groups)} (station, date) pairs…")
    enriched_all   = []
    failed_groups  = []
    discovered_db  = {}    # ICAO → metadata
    forecast_cache = {}    # (icao,date) → consensus_max_c

    for i, ((icao, date_str), group_markets) in enumerate(sorted(groups.items())):
        progress.progress((i + 1) / len(groups),
                          text=f"({i+1}/{len(groups)}) {icao} on {date_str}…")
        try:
            station = fetch_station_info(icao)
            lat, lon = station["lat"], station["lon"]

            # Record station for export
            sample = group_markets[0]
            discovered_db[icao] = {
                "name":             station.get("name", ""),
                "city":             station.get("state", ""),
                "country":          station.get("country", ""),
                "lat":              lat,
                "lon":              lon,
                "wunderground_url": sample.get("resolution", {}).get("wunderground_url", ""),
                "polymarket_aliases": [],  # human to fill
                "markets_seen":     len(group_markets),
            }

            # Deterministic forecasts
            model_results = [
                fetch_model_with_fallback(lat, lon, date_str, mdl)
                for mdl in MODELS
            ]
            valid_det = [r for r in model_results if r.get("max_c") is not None]
            consensus_c = (sum(r["max_c"] for r in valid_det) / len(valid_det)
                           if valid_det else None)
            forecast_cache[(icao, date_str)] = consensus_c

            # Probability distribution
            try:
                pooled, by_model = fetch_super_ensemble(lat, lon, date_str)
            except Exception:
                pooled, by_model = [], {}

            if len(pooled) >= 10:
                samples = pooled
            else:
                samples, _, _ = compute_det_distribution(model_results)

            # Edges per market in group
            enriched = enrich_markets(group_markets, model_results, samples, by_model)
            for em in enriched:
                em["icao"]          = icao
                em["station_name"]  = station.get("name", "")
                em["target_date"]   = date_str
                em["consensus_c"]   = consensus_c
                em["consensus_f"]   = (consensus_c * 9/5 + 32) if consensus_c is not None else None
            enriched_all.extend(enriched)
        except Exception as e:
            failed_groups.append({"icao": icao, "date": date_str, "error": str(e),
                                  "markets": group_markets})

    progress.empty()

    # Build ranked dataframe
    sortable = [m for m in enriched_all if m.get("edge") is not None]
    sortable.sort(key=lambda m: abs(m["edge"]), reverse=True)

    if not sortable:
        st.warning("No markets had a computable edge.")
    else:
        st.markdown(f"### 🎯 {len(sortable)} markets ranked by |edge|")

        rows = []
        for m in sortable:
            edge_pp = m["edge"] * 100
            if edge_pp >= 10:    action = "🟢 BUY YES"
            elif edge_pp >= 5:   action = "🟡 small YES"
            elif edge_pp <= -10: action = "🔴 BUY NO"
            elif edge_pp <= -5:  action = "🟠 small NO"
            else:                action = "—"

            rows.append({
                "Edge pp":   f"{edge_pp:+.1f}",
                "Action":    action,
                "Mkt":       f"{m['yes_price']*100:.1f}%",
                "Model":     f"{m['model_prob']*100:.1f}%" if m.get("model_prob") else "—",
                "ICAO":      m["icao"],
                "Date":      m["target_date"],
                "Forecast Hi °F": f"{m['consensus_f']:.1f}" if m.get("consensus_f") else "—",
                "Station":   (m.get("station_name") or "")[:32],
                "Question":  m["question"][:80] + ("…" if len(m["question"]) > 80 else ""),
                "URL":       m["url"],
            })

        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn("Link", display_text="↗"),
            },
            height=min(700, 60 + 35 * len(rows)),
        )

    # Issues panel
    skipped = len(no_resolution) + len(no_bucket) + len(no_date) + len(failed_groups)
    if skipped:
        with st.expander(f"⚠ {skipped} markets/groups skipped"):
            if no_resolution:
                st.markdown(f"**{len(no_resolution)} markets — resolution station not detected in description:**")
                for m in no_resolution[:8]:
                    st.caption(f"• {m['question'][:120]}")
            if no_bucket:
                st.markdown(f"**{len(no_bucket)} markets — temperature bucket not parsed:**")
                for m in no_bucket[:5]:
                    st.caption(f"• {m['question'][:120]}")
            if no_date:
                st.markdown(f"**{len(no_date)} markets — date out of forecast window or unparseable:**")
                for m in no_date[:5]:
                    st.caption(f"• {m['question'][:120]}")
            if failed_groups:
                st.markdown(f"**{len(failed_groups)} forecast groups failed:**")
                for f in failed_groups[:5]:
                    st.caption(f"• {f['icao']} on {f['date']}: {f['error'][:120]}")

    # JSON export — paste into polymarket_stations.json
    if discovered_db:
        existing = load_stations_db()
        new_icaos = [icao for icao in discovered_db if icao not in existing]

        with st.expander(
            f"💾 Discovered {len(discovered_db)} stations  ·  "
            f"{len(new_icaos)} new (not in polymarket_stations.json)"
        ):
            st.caption(
                "Copy this JSON snippet into `streamlit-app/polymarket_stations.json` "
                "under `\"stations\"` to make these aliases available in the City tab."
            )
            export = {
                icao: {
                    "name":              info["name"],
                    "city":              info.get("city", ""),
                    "country":           info.get("country", ""),
                    "wunderground_url":  info.get("wunderground_url", ""),
                    "polymarket_aliases": [],
                    "markets_seen":      info["markets_seen"],
                    "verified":          True,
                    "_status":           "new" if icao in new_icaos else "already in DB",
                }
                for icao, info in sorted(discovered_db.items())
            }
            st.json(export)


# ── Page layout ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Polymarket Weather Checker",
    page_icon="☁️",
    layout="wide",
)

st.title("☁️ Polymarket Weather Checker")
st.caption("ECMWF IFS · GFS-GraphCast · GFS  vs  Polymarket temperature markets")

tab0, tab1, tab2 = st.tabs([
    "🔍 Discover All Markets",
    "🏙️ By City & Date",
    "🔗 By Polymarket URL",
])

def _build_station_options() -> list[str]:
    """Combo box options: DB (verified) + hardcoded fallback, deduplicated by ICAO."""
    options: dict[str, str] = {}
    # 1. JSON DB — verified from market descriptions
    for icao, info in load_stations_db().items():
        city = info.get("city", "")
        name = info.get("name", "")
        label = f"{icao} — {city} ({name})" if city else f"{icao} — {name}"
        options[icao] = label
    # 2. Hardcoded fallback (guess mappings — not verified)
    for alias, info in POLYMARKET_STATIONS.items():
        icao = info["icao"]
        if icao not in options:
            options[icao] = f"{icao} — {info['name']}  ⚠ unverified"
    return sorted(options.values())


with tab0:
    st.markdown("### Scan all active Polymarket weather markets")
    st.caption(
        "Queries the Polymarket Gamma API, extracts the **exact resolution station** "
        "from each market's description (Wunderground URL → ICAO), forecasts that station, "
        "and ranks all markets by |edge|. YES + NO prices shown for each bucket."
    )

    # Quick station picker — for targeted single-station analysis
    station_opts = _build_station_options()
    if station_opts:
        with st.expander("⚡ Quick analysis — pick a specific station"):
            qcol1, qcol2 = st.columns([3, 1])
            with qcol1:
                chosen_station = st.selectbox(
                    "Station",
                    options=station_opts,
                    index=None,
                    placeholder="Select a Polymarket station…",
                    key="quick_station",
                )
            with qcol2:
                today_q  = date.today()
                quick_dt = st.date_input(
                    "Date",
                    value=today_q,
                    min_value=today_q - timedelta(days=1),
                    max_value=today_q + timedelta(days=16),
                    key="quick_date",
                )
            if st.button("Analyze this station", key="btn_quick"):
                if chosen_station:
                    icao = chosen_station.split("—")[0].strip().split()[0]
                    run_analysis(icao, quick_dt.strftime("%Y-%m-%d"))

    st.divider()
    if st.button("🔍 Scan ALL active weather markets", type="primary", key="btn_discover"):
        run_discover_all()
    else:
        db = load_stations_db()
        if db:
            st.caption(f"Known stations in DB: **{', '.join(sorted(db.keys()))}**")

with tab1:
    col1, col2 = st.columns([3, 1])
    with col1:
        city_input = st.text_input(
            "Airport code, station, or city",
            placeholder="KLAX, NYC, KDEN, Miami…",
            help="Type a Polymarket airport code (KLAX, KNYC, KDEN…) for resolution-grade "
                 "data from the exact METAR station Polymarket uses. City names also work "
                 "but use gridded forecast.",
            key="city",
        )
    with col2:
        today    = date.today()
        sel_date = st.date_input("Date", value=today,
                                 min_value=today - timedelta(days=1),
                                 max_value=today + timedelta(days=16), key="date")

    if st.button("Get Forecast & Compare", type="primary", key="btn_city"):
        if city_input:
            run_analysis(city_input, sel_date.strftime("%Y-%m-%d"))
        else:
            st.warning("Please enter a city name.")

with tab2:
    poly_url = st.text_input("Polymarket event URL",
                             placeholder="https://polymarket.com/event/…", key="url")

    if st.button("Analyze Market", type="primary", key="btn_url"):
        if not poly_url:
            st.warning("Please paste a Polymarket URL.")
        else:
            slug = slug_from_url(poly_url)
            if not slug:
                st.error("Invalid URL. Expected: https://polymarket.com/event/some-slug")
            else:
                with st.spinner("Fetching event from Polymarket…"):
                    try:
                        event   = fetch_poly_event(slug)
                        markets = parse_markets([event])
                        if not markets:
                            st.error("No temperature/weather markets found in this event.")
                        else:
                            # Best path: use the resolution station from the
                            # market's own description (most accurate)
                            location_input = None
                            station_info = None
                            for m in markets:
                                if m.get("resolution") and m["resolution"].get("icao"):
                                    station_info = m["resolution"]
                                    location_input = station_info["icao"]
                                    break

                            if station_info:
                                st.success(
                                    f"✅ Resolution station detected from market: "
                                    f"**{station_info['icao']}**"
                                    + (f" — {station_info['station_name']}"
                                       if station_info.get("station_name") else "")
                                )
                                if station_info.get("wunderground_url"):
                                    st.caption(
                                        f"Source: [{station_info['wunderground_url']}]"
                                        f"({station_info['wunderground_url']})"
                                    )

                            # Fall back to question/slug parsing if no resolution found
                            date_str = None
                            for m in markets:
                                date_str = date_str or parse_date_from_question(m["question"])
                                if date_str:
                                    break
                            if not date_str:
                                date_str = parse_date_from_slug(slug)
                            slug_date = parse_date_from_slug(slug)
                            if slug_date and date_str and slug_date[:4] != date_str[:4]:
                                date_str = slug_date

                            if not location_input:
                                # No resolution URL detected — fall back to city name
                                for m in markets:
                                    location_input = (parse_city_from_question(m["question"])
                                                      or parse_city_from_slug(slug))
                                    if location_input:
                                        break
                                if location_input:
                                    st.warning(
                                        f"⚠ Could not auto-detect resolution station from market "
                                        f"description. Falling back to city geocoding for "
                                        f"'{location_input}' — this may not match Polymarket's "
                                        f"actual resolution source."
                                    )

                            if not location_input:
                                st.error(f"Could not extract location from: {markets[0]['question']}")
                            elif not date_str:
                                st.error(f"Could not extract date from: {markets[0]['question']}")
                            else:
                                run_analysis(location_input, date_str, markets_override=markets)
                    except Exception as e:
                        st.error(f"Error: {e}")
