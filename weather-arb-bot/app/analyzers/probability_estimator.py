import logging
import math
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_DET_SOURCE_KEYS = (
    "wunderground_forecast",
    "gfs_forecast",
    "ecmwf_forecast",
    "hrrr_forecast",
    "icon_forecast",
    "nws_forecast",
    "tomorrowio_forecast",
    "meteosource_forecast",
)

# Below this many live sources, P is shrunk toward 0.5 (10% per missing source)
_SPARSE_SOURCE_BASELINE = 3


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _is_celsius_label(label: str) -> bool:
    return "°C" in (label or "")


def _bucket_to_f_bounds(bmin: Optional[int], bmax: Optional[int], unit: str = "F") -> tuple:
    if unit == "C":
        lo = _c_to_f(float(bmin)) if bmin is not None else -999.0
        hi = _c_to_f(float(bmax)) if bmax is not None else 999.0
    else:
        lo = float(bmin) if bmin is not None else -999.0
        hi = float(bmax) if bmax is not None else 999.0
    return lo, hi


def _gaussian_bucket_prob(forecast_f: float, lo_f: float, hi_f: float, sigma: float = 3.0) -> float:
    sq2 = math.sqrt(2)
    p = 0.5 * (
        math.erf((hi_f - forecast_f) / (sigma * sq2))
        - math.erf((lo_f - forecast_f) / (sigma * sq2))
    )
    return max(0.01, min(0.99, p))


def _clip(p: float, lo: float = 0.03, hi: float = 0.97) -> float:
    return max(lo, min(hi, p))


def estimate_with_breakdown(
    signals: dict,
    bucket_min: Optional[int],
    bucket_max: Optional[int],
    bucket_unit: str = "F",
) -> Tuple[float, dict]:
    """Return (p, breakdown) where breakdown logs P at each stage for debugging."""
    lo_f, hi_f = _bucket_to_f_bounds(bucket_min, bucket_max, bucket_unit)
    bucket_requires_warmth = lo_f >= 66.0
    breakdown: dict = {
        "bucket": {"lo_f": round(lo_f, 2), "hi_f": round(hi_f, 2), "unit": bucket_unit, "warm": bucket_requires_warmth},
        "sources": {},
        "stages": [],
    }

    # --- Deterministic forecast blend ---
    det_probs = []
    for key in _DET_SOURCE_KEYS:
        fc = signals.get(key) or {}
        val = fc.get("predicted_high_f")
        if val is not None:
            sp = _gaussian_bucket_prob(float(val), lo_f, hi_f)
            det_probs.append(sp)
            breakdown["sources"][key] = {"high_f": val, "p": round(sp, 4)}

    if det_probs:
        p = sum(det_probs) / len(det_probs)
        breakdown["stages"].append({"stage": "det_avg", "n": len(det_probs), "p": round(p, 4)})
        if len(det_probs) < _SPARSE_SOURCE_BASELINE:
            missing = _SPARSE_SOURCE_BASELINE - len(det_probs)
            shrink = 1.0 - missing * 0.10
            p = 0.5 + (p - 0.5) * shrink
            breakdown["stages"].append({"stage": "sparse_shrink", "shrink": shrink, "p": round(p, 4)})
    else:
        p = 0.33
        breakdown["stages"].append({"stage": "no_sources_default", "p": round(p, 4)})

    # --- METAR today running-max override ---
    today_max = signals.get("metar_today_max_f")
    if today_max is not None:
        if today_max >= hi_f:
            p = 0.03
            breakdown["stages"].append({"stage": "today_max_ceiling_cleared", "today_max": today_max, "p": round(p, 4)})
            return p, breakdown
        if today_max >= lo_f:
            new_p = max(p, 0.60)
            if new_p != p:
                breakdown["stages"].append({"stage": "today_max_in_bucket_floor", "today_max": today_max, "p": round(new_p, 4)})
            p = new_p

    # --- METAR trend ---
    trend = signals.get("metar_trend") or {}
    rate = trend.get("temp_rate_per_hour", 0.0) or 0.0
    current_temp = trend.get("current_temp_f")
    if current_temp is not None:
        projected = current_temp + rate * 3.0
        proj_p = _gaussian_bucket_prob(projected, lo_f, hi_f)
        p = 0.85 * p + 0.15 * proj_p
        breakdown["stages"].append({"stage": "metar_trend", "projected_f": round(projected, 2), "p": round(p, 4)})

    # --- Reference wind ---
    ref = signals.get("reference_metar") or {}
    ref_wind_dir = ref.get("wind_direction")
    ref_wind_kt = ref.get("wind_speed_kt", 0) or 0
    if ref_wind_dir is not None and ref_wind_kt > 8:
        onshore = 270 <= ref_wind_dir <= 340
        if onshore and bucket_requires_warmth:
            p = _clip(p * 0.75)
            breakdown["stages"].append({"stage": "onshore_cooling", "p": round(p, 4)})
        elif onshore and not bucket_requires_warmth:
            p = _clip(p * 1.15)
            breakdown["stages"].append({"stage": "onshore_favors_cold", "p": round(p, 4)})

    # --- Dew point convergence ---
    primary = signals.get("primary_metar") or {}
    temp_f = primary.get("temperature_f")
    dew_f = primary.get("dew_point_f")
    if temp_f is not None and dew_f is not None:
        dew_spread = temp_f - dew_f
        if dew_spread < 3.0 and bucket_requires_warmth:
            p = _clip(p * 0.88)
            breakdown["stages"].append({"stage": "dew_convergence", "spread": round(dew_spread, 2), "p": round(p, 4)})

    # --- Low-altitude PIREP ---
    pireps = signals.get("pireps") or []
    low_level_pireps = [
        r for r in pireps
        if r.get("flight_level_ft") is not None and r["flight_level_ft"] <= 5000
        and r.get("temperature_c") is not None
    ]
    if low_level_pireps:
        avg_temp_c = sum(r["temperature_c"] for r in low_level_pireps) / len(low_level_pireps)
        avg_temp_f = avg_temp_c * 9 / 5 + 32
        pirep_p = _gaussian_bucket_prob(avg_temp_f, lo_f, hi_f)
        p = 0.95 * p + 0.05 * pirep_p
        breakdown["stages"].append({"stage": "pirep_blend", "avg_f": round(avg_temp_f, 2), "p": round(p, 4)})
        if temp_f is not None and avg_temp_f > temp_f + 5.0 and bucket_requires_warmth:
            p = _clip(p * 0.90)
            breakdown["stages"].append({"stage": "inversion_penalty", "delta": round(avg_temp_f - temp_f, 2), "p": round(p, 4)})

    final = _clip(p)
    if final != p:
        breakdown["stages"].append({"stage": "final_clip", "p": round(final, 4)})
    breakdown["final"] = round(final, 4)
    return final, breakdown


def estimate_true_probability(
    signals: dict,
    bucket_min: Optional[int],
    bucket_max: Optional[int],
    bucket_unit: str = "F",
) -> float:
    p, _ = estimate_with_breakdown(signals, bucket_min, bucket_max, bucket_unit)
    return p
