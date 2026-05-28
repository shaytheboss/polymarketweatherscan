import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

_DET_SOURCE_KEYS = (
    "wunderground_forecast",
    "gfs_forecast",
    "ecmwf_forecast",
    "hrrr_forecast",
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


def _bucket_to_f_bounds(
    bmin: Optional[int],
    bmax: Optional[int],
    unit: str = "F",
) -> tuple:
    """Return (lo_f, hi_f) float bounds in °F for Gaussian math.
    For 'C' buckets, native °C integers are converted exactly to °F.
    """
    if unit == "C":
        lo = _c_to_f(float(bmin)) if bmin is not None else -999.0
        hi = _c_to_f(float(bmax)) if bmax is not None else 999.0
    else:
        lo = float(bmin) if bmin is not None else -999.0
        hi = float(bmax) if bmax is not None else 999.0
    return lo, hi


def _gaussian_bucket_prob(
    forecast_f: float,
    lo_f: float,
    hi_f: float,
    sigma: float = 3.0,
) -> float:
    """P(actual high in [lo_f, hi_f)) given point forecast with Gaussian uncertainty."""
    sq2 = math.sqrt(2)
    p = 0.5 * (
        math.erf((hi_f - forecast_f) / (sigma * sq2))
        - math.erf((lo_f - forecast_f) / (sigma * sq2))
    )
    return max(0.01, min(0.99, p))


def _clip(p: float, lo: float = 0.03, hi: float = 0.97) -> float:
    return max(lo, min(hi, p))


def estimate_true_probability(
    signals: dict,
    bucket_min: Optional[int],
    bucket_max: Optional[int],
    bucket_unit: str = "F",
) -> float:
    lo_f, hi_f = _bucket_to_f_bounds(bucket_min, bucket_max, bucket_unit)
    bucket_requires_warmth = lo_f >= 66.0

    # --- Deterministic forecast blend ---
    det_probs = []
    for key in _DET_SOURCE_KEYS:
        fc = signals.get(key) or {}
        val = fc.get("predicted_high_f")
        if val is not None:
            det_probs.append(_gaussian_bucket_prob(float(val), lo_f, hi_f))

    if det_probs:
        p = sum(det_probs) / len(det_probs)
        # Sparse source shrinkage: fewer than baseline sources → shrink toward 0.5
        if len(det_probs) < _SPARSE_SOURCE_BASELINE:
            missing = _SPARSE_SOURCE_BASELINE - len(det_probs)
            shrink = 1.0 - missing * 0.10
            p = 0.5 + (p - 0.5) * shrink
    else:
        p = 0.33

    # --- METAR today running-max override (same-day certainty anchor) ---
    today_max = signals.get("metar_today_max_f")
    if today_max is not None:
        if today_max >= hi_f:
            # Max already cleared bucket ceiling → definitive NO
            return 0.03
        if today_max >= lo_f:
            # Max is already inside the bucket → YES is still live, raise floor
            p = max(p, 0.60)

    # --- METAR trend adjustment ---
    trend = signals.get("metar_trend") or {}
    rate = trend.get("temp_rate_per_hour", 0.0) or 0.0
    current_temp = trend.get("current_temp_f")
    if current_temp is not None:
        projected = current_temp + rate * 3.0
        proj_p = _gaussian_bucket_prob(projected, lo_f, hi_f)
        p = 0.85 * p + 0.15 * proj_p

    # --- Reference wind adjustment ---
    ref = signals.get("reference_metar") or {}
    ref_wind_dir = ref.get("wind_direction")
    ref_wind_kt = ref.get("wind_speed_kt", 0) or 0
    if ref_wind_dir is not None and ref_wind_kt > 8:
        onshore = 270 <= ref_wind_dir <= 340
        if onshore and bucket_requires_warmth:
            p = _clip(p * 0.75)
        elif onshore and not bucket_requires_warmth:
            p = _clip(p * 1.15)

    # --- Dew point convergence: near-saturated air suppresses daytime heating ---
    primary = signals.get("primary_metar") or {}
    temp_f = primary.get("temperature_f")
    dew_f = primary.get("dew_point_f")
    if temp_f is not None and dew_f is not None:
        dew_spread = temp_f - dew_f
        if dew_spread < 3.0 and bucket_requires_warmth:
            p = _clip(p * 0.88)

    # --- Low-altitude PIREP: Gaussian blend + inversion signal ---
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
        # Temperature inversion: low PIREP warmer than surface → cold air trapped at surface
        if temp_f is not None and avg_temp_f > temp_f + 5.0 and bucket_requires_warmth:
            p = _clip(p * 0.90)

    return _clip(p)
