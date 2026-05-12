"""Unit tests for paper-trade P&L math and resolution logic."""
import pytest

from app.models.paper_trade import compute_pnl
from app.analyzers.paper_trade_settler import _bucket_wins


class _Outcome:
    def __init__(self, bmin, bmax):
        self.bucket_min = bmin
        self.bucket_max = bmax


def test_yes_win_pnl_proportional_to_size():
    # Buy YES at 0.25 with $100 → 400 shares. Win pays $400, P&L = +$300.
    assert compute_pnl("YES", entry_price=0.25, won=True, size_usd=100) == 300.0


def test_yes_loss_pnl_caps_at_size():
    # Buy YES at 0.25 with $100 → loss is -$100.
    assert compute_pnl("YES", entry_price=0.25, won=False, size_usd=100) == -100.0


def test_no_side_pricing_inverts():
    # NO at 0.30 means cost = 0.70 per share. $100 / 0.70 = 142.86 shares.
    # Win → 142.86 * (1 - 0.70) ≈ $42.86. Loss → -$100.
    pnl_win = compute_pnl("NO", entry_price=0.30, won=True, size_usd=100)
    pnl_loss = compute_pnl("NO", entry_price=0.30, won=False, size_usd=100)
    assert pnl_win == pytest.approx(42.86, abs=0.01)
    assert pnl_loss == pytest.approx(-100.0, abs=0.01)


def test_pnl_zero_for_degenerate_prices():
    # Cost of 0 or 1 is degenerate (already settled); return 0 rather than div-by-zero.
    assert compute_pnl("YES", entry_price=0.0, won=True, size_usd=100) == 0.0
    assert compute_pnl("YES", entry_price=1.0, won=False, size_usd=100) == 0.0


def test_bucket_wins_inclusive_half_degree():
    # Bucket 64-65 should include 63.5 .. 65.499...
    o = _Outcome(64, 65)
    assert _bucket_wins(o, 64.0) is True
    assert _bucket_wins(o, 65.4) is True
    assert _bucket_wins(o, 65.5) is False
    assert _bucket_wins(o, 63.5) is True
    assert _bucket_wins(o, 63.4) is False


def test_bucket_open_ended_upper():
    # "66 or higher" → bucket_min=66, bucket_max=None
    o = _Outcome(66, None)
    assert _bucket_wins(o, 65.0) is False
    assert _bucket_wins(o, 65.5) is True  # 65.5 rounds to 66
    assert _bucket_wins(o, 95.0) is True


def test_bucket_open_ended_lower():
    o = _Outcome(None, 60)
    assert _bucket_wins(o, 60.4) is True
    assert _bucket_wins(o, 60.5) is False
