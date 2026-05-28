from typing import Optional

_ALL_FORECAST_KEYS = (
    "wunderground_forecast",
    "gfs_forecast",
    "ecmwf_forecast",
    "hrrr_forecast",
    "icon_forecast",
    "nws_forecast",
    "tomorrowio_forecast",
    "meteosource_forecast",
)


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def compute_confidence(
    signals: dict,
    bucket_min: Optional[int],
    bucket_max: Optional[int],
    bucket_unit: str = "F",
) -> int:
    if bucket_unit == "C":
        lo_f = _c_to_f(float(bucket_min)) if bucket_min is not None else -999.0
        hi_f = _c_to_f(float(bucket_max)) if bucket_max is not None else 999.0
    else:
        lo_f = float(bucket_min) if bucket_min is not None else -999.0
        hi_f = float(bucket_max) if bucket_max is not None else 999.0

    bucket_requires_warmth = lo_f >= 66.0
    score = 40

    # --- Ensemble spread across all available forecast sources ---
    all_highs = []
    for key in _ALL_FORECAST_KEYS:
        fc = signals.get(key) or {}
        val = fc.get("predicted_high_f")
        if val is not None:
            all_highs.append(float(val))

    if len(all_highs) >= 4:
        # Rich ensemble bonus
        score += 10
    if len(all_highs) >= 2:
        spread = max(all_highs) - min(all_highs)
        if spread <= 2:
            score += 20
        elif spread <= 5:
            score += 10
        else:
            score -= 10

    # --- METAR today running-max anchors confidence ---
    today_max = signals.get("metar_today_max_f")
    if today_max is not None:
        if today_max >= hi_f or today_max >= lo_f:
            # Today's observed max already in/past the bucket — strong anchor
            score += 20

    trend = signals.get("metar_trend") or {}
    rate = trend.get("temp_rate_per_hour", 0.0) or 0.0
    current_temp = trend.get("current_temp_f")

    if current_temp is not None:
        if (rate > 0.5 and bucket_requires_warmth) or (rate < -0.5 and not bucket_requires_warmth):
            score += 15
        elif (rate < -0.5 and bucket_requires_warmth) or (rate > 0.5 and not bucket_requires_warmth):
            score -= 15

    ref = signals.get("reference_metar") or {}
    ref_wind_dir = ref.get("wind_direction")
    ref_wind_kt = ref.get("wind_speed_kt", 0) or 0

    if ref_wind_dir is not None:
        onshore = 270 <= ref_wind_dir <= 340
        if onshore and ref_wind_kt > 8:
            if not bucket_requires_warmth:
                score += 15
            else:
                score -= 10
        elif not onshore and ref_wind_kt > 8:
            if bucket_requires_warmth:
                score += 15

    pireps = signals.get("pireps") or []
    low_pireps = [
        r for r in pireps
        if (r.get("flight_level_ft") or 99999) <= 5000
        and r.get("temperature_c") is not None
    ]
    if low_pireps:
        avg_c = sum(r["temperature_c"] for r in low_pireps) / len(low_pireps)
        avg_f = avg_c * 9 / 5 + 32
        if (avg_f > 65 and bucket_requires_warmth) or (avg_f < 60 and not bucket_requires_warmth):
            score += 10
        elif (avg_f < 55 and bucket_requires_warmth) or (avg_f > 68 and not bucket_requires_warmth):
            score -= 5

    price_info = signals.get("market_price") or {}
    yes_price = price_info.get("yes_price")
    if yes_price is not None:
        if 0.05 <= yes_price <= 0.95:
            score += 10

    return max(0, min(100, score))
