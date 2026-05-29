import re
from datetime import datetime, timezone
from typing import Optional

_FORECAST_SOURCE_LABELS = {
    "wunderground_forecast": "WU",
    "gfs_forecast": "GFS",
    "ecmwf_forecast": "ECMWF",
    "hrrr_forecast": "HRRR",
    "icon_forecast": "ICON",
    "nws_forecast": "NWS",
    "tomorrowio_forecast": "Tomorrow.io",
    "meteosource_forecast": "Meteosource",
}


def _c_bucket_f_label(bucket_label: str) -> str:
    if "°C" not in bucket_label:
        return bucket_label
    m = re.search(r'(\d+(?:\.\d+)?)\s*°C\s+or\s+higher', bucket_label, re.IGNORECASE)
    if m:
        f_val = round(float(m.group(1)) * 9 / 5 + 32)
        return f"{bucket_label} (≥{f_val}°F)"
    m = re.search(r'(\d+(?:\.\d+)?)\s*°C\s+or\s+lower', bucket_label, re.IGNORECASE)
    if m:
        f_val = round(float(m.group(1)) * 9 / 5 + 32)
        return f"{bucket_label} (≤{f_val}°F)"
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*°C', bucket_label, re.IGNORECASE)
    if m:
        f1 = round(float(m.group(1)) * 9 / 5 + 32)
        f2 = round(float(m.group(2)) * 9 / 5 + 32)
        return f"{bucket_label} ({f1}-{f2}°F)"
    m = re.search(r'(\d+(?:\.\d+)?)\s*°C', bucket_label)
    if m:
        f_val = round(float(m.group(1)) * 9 / 5 + 32)
        return f"{bucket_label} (≈{f_val}°F)"
    return bucket_label


def _fmt_breakdown(breakdown: dict) -> str:
    stages = breakdown.get("stages") or []
    if not stages:
        return ""
    lines = []
    for s in stages:
        stage = s.get("stage", "?")
        p = s.get("p")
        extras = [f"{k}={v}" for k, v in s.items() if k not in ("stage", "p")]
        extra_str = (" " + " ".join(extras)) if extras else ""
        lines.append(f"  {stage}: p={p}{extra_str}")
    return "\n".join(lines)


def fmt_opportunity(
    city_name, market_question, bucket_label, market_price, true_prob,
    edge, confidence, signals, resolution_time=None, market_url=None,
    side="YES",
) -> str:
    edge_pct = round(edge * 100)
    prob_pct = round(true_prob * 100) if side == "YES" else round((1 - true_prob) * 100)
    display_price = market_price if side == "YES" else round(1 - market_price, 4)
    price_cents = round(display_price * 100)
    date_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    bucket_display = _c_bucket_f_label(bucket_label)

    # F2: Confidence interval from ensemble spread
    forecast_std = signals.get("forecast_std_dev")
    if forecast_std is not None and forecast_std > 0:
        uncertainty_pp = max(1, round(float(forecast_std) * 3.5))
        prob_str = f"{prob_pct}% ±{uncertainty_pp}pp"
    else:
        prob_str = f"{prob_pct}%"

    key_signals = []

    ref = signals.get("reference_metar") or {}
    if ref.get("wind_direction") and ref.get("wind_speed_kt"):
        key_signals.append(f"• Ref station wind {ref['wind_direction']:03d}°/{ref['wind_speed_kt']}kt")

    trend = signals.get("metar_trend") or {}
    primary = signals.get("primary_metar") or {}
    temp_f = primary.get("temperature_f")
    dew_f = primary.get("dew_point_f")
    if dew_f is not None and trend.get("dew_rate_per_hour") and abs(trend["dew_rate_per_hour"]) > 0.3:
        direction = "rising" if trend["dew_rate_per_hour"] > 0 else "falling"
        key_signals.append(f"• Dew point {direction} ({dew_f}°F)")
    if temp_f is not None and dew_f is not None and (temp_f - dew_f) < 5.0:
        key_signals.append(f"• Dew spread {round(temp_f - dew_f, 1)}°F — fog/stratus risk")

    today_max = signals.get("metar_today_max_f")
    if today_max is not None:
        key_signals.append(f"• METAR today max: {round(today_max, 1)}°F")

    pireps = signals.get("pireps") or []
    low_pireps = [p for p in pireps if (p.get("flight_level_ft") or 99999) <= 5000]
    if low_pireps:
        valid = [p["temperature_c"] for p in low_pireps if p.get("temperature_c") is not None]
        if valid:
            avg_f = round(sum(valid) / len(valid) * 9 / 5 + 32)
            key_signals.append(f"• PIREP: {avg_f}°F avg at low altitude ({len(valid)} reports)")

    forecast_parts = []
    for key, label in _FORECAST_SOURCE_LABELS.items():
        fc = signals.get(key) or {}
        val = fc.get("predicted_high_f")
        if val is not None:
            forecast_parts.append(f"{label}: {val}°F")
    if forecast_parts:
        key_signals.append(f"• Forecasts ({len(forecast_parts)}): {' | '.join(forecast_parts)}")

    signals_text = "\n".join(key_signals) if key_signals else "• No key signals available"

    # F3: Why now
    why_now = signals.get("why_now") or []
    why_str = f"\n\U0001f4a1 Why now: {', '.join(why_now)}" if why_now else ""

    # Breakdown stages
    breakdown_text = ""
    breakdown = signals.get("probability_breakdown")
    if breakdown:
        b = _fmt_breakdown(breakdown)
        if b:
            breakdown_text = f"\n\n\U0001f9ee Probability stages:\n{b}"

    hours_left = ""
    if resolution_time:
        delta = resolution_time - datetime.now(timezone.utc)
        h = int(delta.total_seconds() // 3600)
        if h > 0:
            hours_left = f"\n⏰ Time to resolution: ~{h}h"
    link_line = f"\n[Polymarket]({market_url})" if market_url else ""

    return (
        f"\U0001f3af *HIGH CONFIDENCE OPPORTUNITY*\n\n"
        f"\U0001f4cd {city_name} | {date_str}\n"
        f"\U0001f4ca Market: {market_question}\n"
        f"\U0001f3b2 Bucket: {bucket_display} [{side}]\n\n"
        f"\U0001f4b0 Market price: {price_cents}¢ ({side})\n"
        f"\U0001f9e0 Model estimate: {prob_str} P({side})\n"
        f"\U0001f4c8 Edge: +{edge_pct}pp"
        f"{why_str}\n\n"
        f"\U0001f50d Key signals:\n{signals_text}"
        f"{breakdown_text}\n\n"
        f"⚠️  Confidence: {confidence}/100"
        f"{hours_left}"
        f"{link_line}"
    )


def fmt_status(city_signals: list) -> str:
    if not city_signals:
        return "No cities currently being monitored."
    lines = ["\U0001f4e1 *Current Status*\n"]
    for cs in city_signals:
        lines.append(f"\U0001f4cd *{cs['city']}*: {cs.get('temp_f', '?')}°F now, forecast {cs.get('forecast_high', '?')}°F")
    return "\n".join(lines)
