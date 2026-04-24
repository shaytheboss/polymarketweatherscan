from datetime import datetime, timezone
from typing import Optional


def fmt_opportunity(
    city_name: str,
    market_question: str,
    bucket_label: str,
    market_price: float,
    true_prob: float,
    edge: float,
    confidence: int,
    signals: dict,
    resolution_time: Optional[datetime] = None,
    market_url: Optional[str] = None,
) -> str:
    edge_pct = round(edge * 100)
    price_cents = round(market_price * 100)
    prob_pct = round(true_prob * 100)

    date_str = datetime.now(timezone.utc).strftime("%b %d, %Y")

    key_signals = []

    # Reference station signal
    ref = signals.get("reference_metar") or {}
    if ref.get("wind_direction") and ref.get("wind_speed_kt"):
        key_signals.append(
            f"• Ref station wind {ref['wind_direction']:03d}°/{ref['wind_speed_kt']}kt"
        )

    # Dew point trend
    trend = signals.get("metar_trend") or {}
    primary = signals.get("primary_metar") or {}
    if trend.get("dew_rate_per_hour") and abs(trend["dew_rate_per_hour"]) > 0.3:
        direction = "rising" if trend["dew_rate_per_hour"] > 0 else "falling"
        dp = primary.get("dew_point_f")
        key_signals.append(f"• Dew point {direction} ({dp}°F)")

    # PIREP
    pireps = signals.get("pireps") or []
    low_pireps = [p for p in pireps if (p.get("flight_level_ft") or 99999) <= 5000]
    if low_pireps:
        avg_c = sum(p["temperature_c"] for p in low_pireps if p.get("temperature_c")) / len(low_pireps)
        avg_f = round(avg_c * 9 / 5 + 32)
        key_signals.append(f"• PIREP: {avg_f}°F avg at low altitude")

    # Wunderground
    wg = signals.get("wunderground_forecast") or {}
    if wg.get("predicted_high_f"):
        key_signals.append(f"• Wunderground forecast: {wg['predicted_high_f']}°F")

    signals_text = "\n".join(key_signals) if key_signals else "• No key signals available"

    hours_left = ""
    if resolution_time:
        delta = resolution_time - datetime.now(timezone.utc)
        h = int(delta.total_seconds() // 3600)
        if h > 0:
            hours_left = f"\n⏰ Time to resolution: ~{h}h"

    link_line = f"\n[Polymarket]({market_url})" if market_url else ""

    return (
        f"🎯 *HIGH CONFIDENCE OPPORTUNITY*\n\n"
        f"📍 {city_name} | {date_str}\n"
        f"📊 Market: {market_question}\n"
        f"🎲 Bucket: {bucket_label} (YES)\n\n"
        f"💰 Market price: {price_cents}¢\n"
        f"🧠 Model estimate: {prob_pct}%\n"
        f"📈 Edge: +{edge_pct}pp\n\n"
        f"🔍 Key signals:\n{signals_text}\n\n"
        f"⚠️  Confidence: {confidence}/100"
        f"{hours_left}"
        f"{link_line}"
    )


def fmt_monitoring_started(city_name: str, market_question: str) -> str:
    return (
        f"👁 *MONITORING STARTED*\n\n"
        f"📍 {city_name}\n"
        f"📊 {market_question}\n\n"
        f"I'll alert you when I find an edge."
    )


def fmt_opportunity_closed(city_name: str, bucket_label: str) -> str:
    return (
        f"✅ *OPPORTUNITY CLOSED*\n\n"
        f"📍 {city_name} | {bucket_label}\n"
        f"Market price returned to fair value."
    )


def fmt_status(city_signals: list) -> str:
    if not city_signals:
        return "No cities currently being monitored."
    lines = ["📡 *Current Status*\n"]
    for cs in city_signals:
        temp = cs.get("temp_f", "?")
        forecast = cs.get("forecast_high", "?")
        lines.append(f"📍 *{cs['city']}*: {temp}°F now, forecast {forecast}°F")
    return "\n".join(lines)
