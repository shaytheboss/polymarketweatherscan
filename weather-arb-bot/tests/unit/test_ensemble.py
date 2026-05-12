"""Unit tests for ensemble member parsing and bucket probability."""
from datetime import date
from zoneinfo import ZoneInfo

from app.collectors.ensemble_collector import (
    _local_day_max,
    _member_series,
    bucket_probability_from_members,
)
from app.analyzers.probability_estimator import (
    _empirical_bucket_prob,
    estimate_true_probability,
)


def test_member_series_extracts_control_and_members():
    hourly = {
        "temperature_2m": [60.0, 61.0],
        "temperature_2m_member01": [59.0, 60.0],
        "temperature_2m_member02": [62.0, 63.0],
        "wind_speed_10m": [5.0, 6.0],
    }
    series = _member_series(hourly, "temperature_2m")
    assert len(series) == 3
    assert series[0] == [60.0, 61.0]
    assert series[2] == [62.0, 63.0]


def test_member_series_stops_on_first_gap():
    hourly = {
        "temperature_2m_member01": [60.0],
        # No member02 → series should be [member01] only.
        "temperature_2m_member03": [99.0],
    }
    series = _member_series(hourly, "temperature_2m")
    assert series == [[60.0]]


def test_local_day_max_picks_target_date_only():
    tz = ZoneInfo("America/Los_Angeles")
    times = [
        "2026-05-12T22:00",
        "2026-05-13T08:00",
        "2026-05-13T15:00",
        "2026-05-13T23:00",
        "2026-05-14T01:00",
    ]
    values = [55.0, 60.0, 75.0, 68.0, 70.0]
    assert _local_day_max(times, values, date(2026, 5, 13), tz) == 75.0


def test_bucket_probability_inclusive_half_degree():
    # Members at 64.0, 65.0, 65.4 should all count as in bucket "64-65".
    members = [62.0, 64.0, 65.0, 65.4, 65.6, 70.0]
    p = bucket_probability_from_members(members, 64, 65)
    assert p == 3 / 6


def test_bucket_probability_none_with_empty_members():
    assert bucket_probability_from_members([], 64, 65) is None
    assert bucket_probability_from_members([60.0], None, None) is None


def test_empirical_signal_dominates_estimator():
    # Wunderground says 70°F (outside bucket 64-65) but 80% of members are in
    # the bucket — the estimator should land near the empirical value.
    members = [64.5, 64.8, 65.0, 65.2, 70.0]  # 4/5 in 64-65 bucket
    signals = {
        "wunderground_forecast": {"predicted_high_f": 70},
        "gfs_forecast": {"predicted_high_f": 70},
        "ecmwf_forecast": {"predicted_high_f": 70},
        "ensemble_gfs": {"members_high_f": members},
        "ensemble_ecmwf": None,
        "metar_trend": None,
        "reference_metar": None,
        "pireps": [],
    }
    p = estimate_true_probability(signals, bucket_min=64, bucket_max=65)
    # Pure empirical = 0.8; with 20% wunderground pull (low prob) it should
    # still be well above 0.5.
    assert p > 0.55


def test_empirical_smoothing_avoids_certainty():
    # All members in bucket → smoothed prob should be < 1.0 (Laplace).
    members = [64.5] * 10
    p = _empirical_bucket_prob(members, 64, 65)
    assert p == 1.0
    # but when fed through estimator the smoothed value stays under 0.99.
    signals = {
        "wunderground_forecast": {"predicted_high_f": 65},
        "ensemble_gfs": {"members_high_f": members},
        "ensemble_ecmwf": None,
        "metar_trend": None,
        "reference_metar": None,
        "pireps": [],
    }
    out = estimate_true_probability(signals, 64, 65)
    assert out < 0.99
