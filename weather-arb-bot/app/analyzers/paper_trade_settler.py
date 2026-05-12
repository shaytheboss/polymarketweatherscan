"""
Close out paper trades once their underlying markets resolve.

Resolution comes from the ``Market.resolved`` / ``Market.resolution_value``
columns, which are populated either by an external resolver job or manually
when Polymarket settles. The settler then walks every open paper trade for
that market and stamps win/loss + P&L.
"""
import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Market, MarketOutcome
from app.models.paper_trade import PaperTrade, compute_pnl

logger = logging.getLogger(__name__)


def _bucket_wins(outcome: MarketOutcome, resolution_high_f: float) -> bool:
    """True iff the resolved value falls in this outcome's bucket."""
    lo = (outcome.bucket_min - 0.5) if outcome.bucket_min is not None else float("-inf")
    hi = (outcome.bucket_max + 0.5) if outcome.bucket_max is not None else float("inf")
    return lo <= resolution_high_f < hi


async def settle_paper_trades_for_market(
    db: AsyncSession, market: Market
) -> List[PaperTrade]:
    """Settle every open paper trade tied to ``market``."""
    if not market.resolved or market.resolution_value is None:
        return []

    try:
        resolution_high = float(market.resolution_value)
    except (TypeError, ValueError):
        logger.warning(
            f"Market {market.id} resolution_value={market.resolution_value!r} "
            "is not numeric; cannot settle paper trades"
        )
        return []

    outcomes_q = await db.execute(
        select(MarketOutcome).where(MarketOutcome.market_id == market.id)
    )
    outcomes = {o.id: o for o in outcomes_q.scalars().all()}

    trades_q = await db.execute(
        select(PaperTrade).where(
            PaperTrade.market_id == market.id,
            PaperTrade.settled == False,  # noqa: E712
        )
    )
    trades = trades_q.scalars().all()

    settled: List[PaperTrade] = []
    for trade in trades:
        outcome = outcomes.get(trade.outcome_id)
        if not outcome:
            continue
        bucket_hit = _bucket_wins(outcome, resolution_high)
        won = bucket_hit if trade.side == "YES" else not bucket_hit

        entry = float(trade.entry_price)
        size = float(trade.size_usd)
        trade.exit_price = 1.0 if won else 0.0
        trade.resolution_outcome = "WIN" if won else "LOSS"
        trade.pnl_usd = compute_pnl(trade.side, entry, won, size)
        trade.closed_at = datetime.now(timezone.utc)
        trade.settled = True
        settled.append(trade)

    if settled:
        await db.commit()
        logger.info(
            f"Settled {len(settled)} paper trades for market {market.id} "
            f"(resolution={resolution_high}°F)"
        )

    return settled
