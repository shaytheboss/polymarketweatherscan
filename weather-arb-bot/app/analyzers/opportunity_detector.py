import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.analyzers.signal_aggregator import SignalAggregator
from app.analyzers.probability_estimator import estimate_true_probability, _is_celsius_label
from app.analyzers.confidence_scorer import compute_confidence
from app.config import settings
from app.models.city import City
from app.models.market import Market, MarketOutcome
from app.models.opportunity import Opportunity

logger = logging.getLogger(__name__)

aggregator = SignalAggregator()


def _required_edge(market_price: float) -> float:
    if 0.30 <= market_price <= 0.70:
        return settings.min_edge_for_alert
    return settings.min_edge_for_alert * 0.67


def _resolve_bucket_unit(outcome: MarketOutcome) -> str:
    """Return 'C' or 'F'. Falls back to label detection when column not yet migrated."""
    unit = (getattr(outcome, "bucket_unit", None) or "F").upper()
    if unit == "F" and _is_celsius_label(outcome.bucket_label or ""):
        unit = "C"
    return unit


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

        for outcome in outcomes:
            try:
                opp = await _analyze_outcome(db, city, market, outcome)
                if opp:
                    found.append(opp)
            except Exception as e:
                logger.error(f"Error analyzing outcome {outcome.id}: {e}", exc_info=True)

    return found


async def _analyze_outcome(
    db: AsyncSession,
    city: City,
    market: Market,
    outcome: MarketOutcome,
) -> Optional[Opportunity]:
    city_lat = float(city.nws_lat) if city.nws_lat is not None else None
    city_lon = float(city.nws_lon) if city.nws_lon is not None else None

    signals = await aggregator.aggregate(
        db=db,
        city_id=city.id,
        primary_icao=city.primary_icao,
        reference_icao=city.reference_icao,
        outcome=outcome,
        forecast_date=market.event_date,
        city_lat=city_lat,
        city_lon=city_lon,
    )

    price_info = signals.get("market_price")
    if not price_info:
        return None

    yes_price = price_info["yes_price"]
    bucket_unit = _resolve_bucket_unit(outcome)

    true_prob = estimate_true_probability(
        signals, outcome.bucket_min, outcome.bucket_max, bucket_unit
    )
    confidence = compute_confidence(
        signals, outcome.bucket_min, outcome.bucket_max, bucket_unit
    )

    yes_edge = true_prob - yes_price
    no_edge = (1 - true_prob) - (1 - yes_price)
    best_edge = yes_edge if yes_edge >= no_edge else no_edge
    best_side = "YES" if yes_edge >= no_edge else "NO"

    req_edge = _required_edge(yes_price)
    if best_edge < req_edge or confidence < settings.min_confidence_for_alert:
        return None

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
