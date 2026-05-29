from sqlalchemy import Column, ForeignKey, Integer, Float, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base


class ForecastAccuracy(Base):
    """B1/B2: Per-(city, source, lead_time, month) accuracy stats for learned sigma and bias correction.

    Populated by a post-resolution job after markets close.
    Used by probability_estimator once enough samples accumulate (recommend >=30).
    """
    __tablename__ = "forecast_accuracy"
    id = Column(Integer, primary_key=True, autoincrement=True)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=False)
    source = Column(String(50), nullable=False)
    lead_time_days = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)        # 1-12
    sample_count = Column(Integer, nullable=False, server_default="0")
    mae = Column(Float, nullable=True)             # Mean Absolute Error °F
    bias = Column(Float, nullable=True)            # Systematic bias °F (positive = warm bias)
    sigma_estimate = Column(Float, nullable=True)  # Learned σ for Student-t; replaces default 3.0°F
    last_updated = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("city_id", "source", "lead_time_days", "month",
                         name="uq_forecast_accuracy"),
    )
