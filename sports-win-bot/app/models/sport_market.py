from sqlalchemy import Column, Integer, String, Float, TIMESTAMP, Boolean
from sqlalchemy.sql import func

from app.database import Base


class SportMarket(Base):
    """A Polymarket sports market (one per match outcome)."""
    __tablename__ = "sport_markets"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Polymarket identifiers
    condition_id = Column(String(80), unique=True, nullable=False, index=True)
    event_slug = Column(String(200), nullable=True)   # for https://polymarket.com/event/{slug}
    token_id_p1 = Column(String(80), nullable=True)   # CLOB YES token (player1 wins)
    token_id_p2 = Column(String(80), nullable=True)   # CLOB NO token  (player2 wins)

    # Match info
    player1 = Column(String(120), nullable=False)
    player2 = Column(String(120), nullable=False)
    sport = Column(String(20), default="tennis")

    # Live prices from Polymarket
    poly_price_p1 = Column(Float, nullable=True)   # P(player1 wins) — range [0, 1]
    poly_price_p2 = Column(Float, nullable=True)

    # Price at last market refresh (used to detect big moves)
    prev_price_p1 = Column(Float, nullable=True)

    is_active = Column(Boolean, default=True)
    last_price_update = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def market_url(self) -> str:
        if self.event_slug:
            return f"https://polymarket.com/event/{self.event_slug}"
        return "https://polymarket.com"

    @property
    def price_move_pct(self) -> float | None:
        """Absolute price move % since last refresh."""
        if self.poly_price_p1 is None or self.prev_price_p1 is None:
            return None
        return abs(self.poly_price_p1 - self.prev_price_p1) * 100
