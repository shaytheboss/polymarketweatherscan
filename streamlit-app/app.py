"""
Polymarket Weather Checker — Streamlit version
Forecasts: ECMWF IFS + GFS-GraphCast + GFS  vs  Polymarket temperature markets
Current conditions: Open-Meteo live data (temp, wind, humidity + timestamp)
"""
import re
import json
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st

# ── Constants ─────────────────────────────────────────────────────────────────

GEOCODE_URL  = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
POLYMARKET   = "https://gamma-api.polymarket.com"
SIGMA        = 2.5   # °C day-ahead forecast uncertainty

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


def parse_markets(events: list, city_filter: str | None = None) -> list:
    results    = []
    fltr_lower = city_filter.lower() if city_filter else None
    for event in events:
        slug = event.get("slug", "")
        for market in event.get("markets", []):
            q  = market.get("question", "")
            ql = q.lower()
            if not any(kw in ql for kw in WEATHER_KW):
                continue
            if fltr_lower and fltr_lower not in ql:
                continue
            prices = [0.5, 0.5]
            try:
                raw    = market.get("outcomePrices")
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list) and parsed:
                    prices = [float(p) for p in parsed]
            except Exception:
                pass
            results.append({
                "question":  q,
                "yes_price": prices[0] if prices else 0.5,
                "url":       f"https://polymarket.com/event/{slug}",
                "bucket":    parse_temp_bucket(q),
                "end_date":  market.get("endDate", event.get("endDate", "")),
            })
    return results


def enrich_markets(markets: list, model_results: list) -> list:
    valid   = [r for r in model_results if r.get("max_c") is not None]
    all_max = [r["max_c"] for r in valid]
    enriched = []
    for m in markets:
        bucket = m.get("bucket")
        if not bucket or not all_max:
            enriched.append({**m, "model_prob": None, "edge": None, "per_model": []})
            continue
        probs = [bucket_probability(fc, bucket.get("min_c"), bucket.get("max_c"))
                 for fc in all_max]
        model_prob = sum(probs) / len(probs)
        enriched.append({
            **m,
            "model_prob": model_prob,
            "edge":       model_prob - m["yes_price"],
            "per_model":  list(zip([r["label"] for r in valid], probs)),
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


def render_markets(markets: list):
    if not markets:
        st.info("No temperature markets found. Try the **By Polymarket URL** tab with a direct link.")
        return

    st.markdown(f"**📊 Polymarket Markets ({len(markets)})**")
    st.caption("Edge = Model probability − Market price (positive → model thinks YES is underpriced)")

    sorted_m = sorted(markets, key=lambda m: abs(m.get("edge") or 0), reverse=True)

    for m in sorted_m:
        edge  = m.get("edge")
        mprob = m.get("model_prob")
        label = fmt_edge(edge)
        with st.expander(f"{label}  —  {m['question'][:90]}{'…' if len(m['question']) > 90 else ''}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Market Price", f"{m['yes_price']*100:.1f}%")
            with c2:
                st.metric("Model Probability", f"{mprob*100:.1f}%" if mprob else "—")
            with c3:
                st.metric("Edge", label)

            if m.get("per_model"):
                per = "  ·  ".join(f"{lbl}: {p*100:.1f}%" for lbl, p in m["per_model"])
                st.caption(f"Per model: {per}")

            if m.get("bucket"):
                st.caption(f"Bucket: {fmt_bucket(m['bucket'])}")

            st.link_button("Open on Polymarket ↗", m["url"])

# ── Main analysis flow ────────────────────────────────────────────────────────

def run_analysis(city_input: str, date_str: str, markets_override=None):
    with st.spinner("Geocoding…"):
        try:
            location = geocode_city(city_input)
        except Exception as e:
            st.error(str(e))
            return None

    with st.container(border=True):
        day_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d, %Y").replace(" 0", " ")
        st.subheader(f"📍 {location['display_name']}")
        st.caption(f"{location['lat']:.4f}°N, {location['lon']:.4f}°E  ·  {day_label}")

    # Current observation
    with st.spinner("Fetching current conditions…"):
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

    enriched = enrich_markets(markets, model_results)
    render_markets(enriched)
    return model_results

# ── Page layout ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Polymarket Weather Checker",
    page_icon="☁️",
    layout="wide",
)

st.title("☁️ Polymarket Weather Checker")
st.caption("ECMWF IFS · GFS-GraphCast · GFS  vs  Polymarket temperature markets")

tab1, tab2 = st.tabs(["🏙️ By City & Date", "🔗 By Polymarket URL"])

with tab1:
    col1, col2 = st.columns([3, 1])
    with col1:
        city_input = st.text_input("City or location", placeholder="Denver, LAX, Tel Aviv…", key="city")
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
                            city = date_str = None
                            for m in markets:
                                city     = city     or parse_city_from_question(m["question"]) or parse_city_from_slug(slug)
                                date_str = date_str or parse_date_from_question(m["question"])
                                if city and date_str:
                                    break
                            if not date_str:
                                date_str = parse_date_from_slug(slug)
                            # Trust slug year if question year differs
                            slug_date = parse_date_from_slug(slug)
                            if slug_date and date_str and slug_date[:4] != date_str[:4]:
                                date_str = slug_date

                            if not city:
                                st.error(f"Could not extract city from: {markets[0]['question']}")
                            elif not date_str:
                                st.error(f"Could not extract date from: {markets[0]['question']}")
                            else:
                                run_analysis(city, date_str, markets_override=markets)
                    except Exception as e:
                        st.error(f"Error: {e}")
