from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database import Base


class PaperTrade(Base):
    """
    A simulated trade opened automatically when an opportunity alert fires.
    Lets us track hypothetical P&L without putting capital at risk —
    essential for backtesting the bot's edge claims before going live.
    """

    __tablename__ = "paper_trades"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    opportunity_id = Column(
        Integer, ForeignKey("opportunities.id"), nullable=False, unique=True
    )
    outcome_id = Column(Integer, ForeignKey("market_outcomes.id"), nullable=False)
    market_id = Column(Integer, ForeignKey("markets.id"), nullable=False)
    city_id = Column(Integer, ForeignKey("cities.id"))

    side = Column(String(3), nullable=False)  # YES | NO
    entry_price = Column(Numeric(6, 4), nullable=False)
    estimated_true_prob = Column(Numeric(6, 4), nullable=False)
    edge_at_entry = Column(Numeric(6, 4), nullable=False)
    confidence_at_entry = Column(Integer, nullable=False)
    size_usd = Column(Numeric(10, 2), nullable=False)

    opened_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    closed_at = Column(TIMESTAMP(timezone=True))
    exit_price = Column(Numeric(6, 4))
    resolution_outcome = Column(String(20))  # WIN | LOSS | VOID
    pnl_usd = Column(Numeric(12, 2))
    settled = Column(Boolean, default=False, nullable=False)

    notes = Column(Text)
    signals_snapshot = Column(JSONB)

    opportunity = relationship("Opportunity")

    __table_args__ = (
        Index("idx_paper_trades_outcome", "outcome_id", "opened_at"),
        Index("idx_paper_trades_open", "settled"),
    )


def compute_pnl(side: str, entry_price: float, won: bool, size_usd: float) -> float:
    """
    Polymarket-style binary: shares cost ``entry_price`` (YES) or
    ``1 - entry_price`` (NO) and pay $1 if the side resolves true.

    P&L per share = (1 - cost) on win, -cost on loss. Scale by number of
    shares = size_usd / cost.
    """
    cost = entry_price if side == "YES" else (1.0 - entry_price)
    if cost <= 0 or cost >= 1:
        return 0.0
    shares = size_usd / cost
    return round(shares * ((1.0 - cost) if won else -cost), 2)
