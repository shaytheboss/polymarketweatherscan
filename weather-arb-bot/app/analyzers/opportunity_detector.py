import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.analyzers.signal_aggregator import SignalAggregator
from app.analyzers.probability_estimator import estimate_with_breakdown, _is_celsius_label
from app.analyzers.confidence_scorer import compute_confidence
from app.config import settings
from app.models.city import City
from app.models.market import Market, MarketOutcome
from app.models.opportunity import Opportunity

logger = logging.getLogger(__name__)

aggregator = SignalAggregator()

_SOURCE_LABELS = {
    "wunderground_forecast": "WU",
    "gfs_forecast": "GFS",
    "ecmwf_forecast": "ECMWF",
    "hrrr_forecast": "HRRR",
    "icon_forecast": "ICON",
    "nws_forecast": "NWS",
    "tomorrowio_forecast": "Tomorrow.io",
    "meteosource_forecast": "Meteosource",
}


def _required_edge(market_price: float) -> float:
    if 0.30 <= market_price <= 0.70:
        return settings.min_edge_for_alert
    return settings.min_edge_for_alert * 0.67


def _resolve_bucket_unit(outcome: MarketOutcome) -> str:
    unit = (getattr(outcome, "bucket_unit", None) or "F").upper()
    if unit == "F" and _is_celsius_label(outcome.bucket_label or ""):
        unit = "C"
    return unit


def _compute_why_now(current_signals: dict, prev_signals: dict) -> list:
    """F3: Diff key forecast values vs. previous opportunity for the same outcome."""
    if not prev_signals:
        return ["first alert for this outcome"]
    changes = []
    for key, label in _SOURCE_LABELS.items():
        curr = (current_signals.get(key) or {}).get("predicted_high_f")
        prev = (prev_signals.get(key) or {}).get("predicted_high_f")
        if curr is not None and prev is not None:
            delta = float(curr) - float(prev)
            if abs(delta) >= 1.0:
                direction = "↑" if delta > 0 else "↓"
                changes.append(f"{label} {direction}{abs(delta):.0f}°F")
    if not changes:
        return ["edge/confidence improved since last alert"]
    return changes


async def detect_opportunities(db: AsyncSession) -> List[Opportunity]:
    found: List[Opportunity] = []

    result = await db.execute(
        select(Market).where(Market.resolved == False).order_by(Market.event_date)
    )
    markets: List[Market] = result.scalars().all()

    for market in markets:
        city_result = await db.execute(select(City).where(City.id == market.city_id))
        city: Optional[City] = city_result.scalar_one_or_none()
        if not city:
            continue

        outcomes_result = await db.execute(
            select(MarketOutcome).where(MarketOutcome.market_id == market.id)
        )
        outcomes: List[MarketOutcome] = outcomes_result.scalars().all()

        # Phase 1: collect signals + raw probs for all outcomes in this market
        market_items = {}  # outcome.id -> (outcome, signals, true_prob, breakdown)
        for outcome in outcomes:
            try:
                data = await _collect_outcome_data(db, city, market, outcome)
                if data:
                    market_items[outcome.id] = data
            except Exception as e:
                logger.error(f"Error collecting outcome {outcome.id}: {e}", exc_info=True)

        if not market_items:
            continue

        # Phase 2: C — normalize probabilities across all buckets so they sum to 1.
        # Catches relative mispricings between buckets (not just absolute vs. truth).
        total_p = sum(item[2] for item in market_items.values())
        if total_p > 0.01 and len(market_items) > 1:
            for oid in list(market_items):
                outcome_, signals_, prob_, breakdown_ = market_items[oid]
                norm_p = prob_ / total_p
                breakdown_["final_normalized"] = round(norm_p, 4)
                market_items[oid] = (outcome_, signals_, norm_p, breakdown_)

        # Phase 3: evaluate edges with normalized probs, attach why_now, persist
        for oid, (outcome, signals, true_prob, breakdown) in market_items.items():
            try:
                opp = await _evaluate_opportunity(db, city, market, outcome, signals, true_prob, breakdown)
                if opp:
                    found.append(opp)
            except Exception as e:
                logger.error(f"Error evaluating opportunity {outcome.id}: {e}", exc_info=True)

    return found


async def _collect_outcome_data(
    db: AsyncSession, city: City, market: Market, outcome: MarketOutcome
) -> Optional[tuple]:
    city_lat = float(city.nws_lat) if city.nws_lat is not None else None
    city_lon = float(city.nws_lon) if city.nws_lon is not None else None
    signals = await aggregator.aggregate(
        db=db, city_id=city.id, primary_icao=city.primary_icao,
        reference_icao=city.reference_icao, outcome=outcome,
        forecast_date=market.event_date, city_lat=city_lat, city_lon=city_lon,
    )
    if not signals.get("market_price"):
        return None
    bucket_unit = _resolve_bucket_unit(outcome)
    true_prob, breakdown = estimate_with_breakdown(
        signals, outcome.bucket_min, outcome.bucket_max, bucket_unit
    )
    return (outcome, signals, true_prob, breakdown)


async def _evaluate_opportunity(
    db: AsyncSession,
    city: City,
    market: Market,
    outcome: MarketOutcome,
    signals: dict,
    true_prob: float,
    breakdown: dict,
) -> Optional[Opportunity]:
    price_info = signals["market_price"]
    yes_price = price_info["yes_price"]
    bucket_unit = _resolve_bucket_unit(outcome)

    confidence = compute_confidence(
        signals, outcome.bucket_min, outcome.bucket_max, bucket_unit
    )
    logger.info(
        "analyzed outcome=%s bucket=%s p=%.3f conf=%d",
        outcome.id, outcome.bucket_label, true_prob, confidence,
    )

    yes_edge = true_prob - yes_price
    no_edge = (1 - true_prob) - (1 - yes_price)
    best_edge = yes_edge if yes_edge >= no_edge else no_edge
    best_side = "YES" if yes_edge >= no_edge else "NO"

    req_edge = _required_edge(yes_price)
    if best_edge < req_edge or confidence < settings.min_confidence_for_alert:
        return None

    # F3: Why now? — compare with most recent prior opportunity for this outcome
    prev_result = await db.execute(
        select(Opportunity)
        .where(Opportunity.outcome_id == outcome.id)
        .order_by(desc(Opportunity.detected_at))
        .limit(1)
    )
    prev_opp = prev_result.scalar_one_or_none()
    signals["why_now"] = _compute_why_now(signals, prev_opp.signals if prev_opp else {})
    signals["probability_breakdown"] = breakdown

    opp = Opportunity(
        outcome_id=outcome.id,
        detected_at=datetime.now(timezone.utc),
        side=best_side,
        market_price=yes_price,
        estimated_true_prob=true_prob,
        edge=best_edge,
        confidence_score=confidence,
        signals=signals,
        alert_sent=False,
    )
    db.add(opp)
    await db.commit()
    await db.refresh(opp)
    return opp
