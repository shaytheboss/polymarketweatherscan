"""
Background scheduler jobs.

Job flow:
  job_refresh_markets (every 5 min):
    → GET /events?tag_id=864  (correct Polymarket endpoint)
    → upsert SportMarket rows
    → restart WebSocket feed with fresh token list

  job_poll_prices (every 30 s):
    → read prices from ws_feed._prices  (if WS is live)
    → fallback to CLOB REST batch fetch
    → detect big price moves → send Telegram alert
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.sport_market import SportMarket
from app.collectors.polymarket import PolyMarket, fetch_tennis_markets, fetch_clob_prices

logger = logging.getLogger(__name__)

# In-memory live market cache (refreshed every 5 min)
_active_markets: list[PolyMarket] = []
_ws_task: asyncio.Task | None = None


# ---------------------------------------------------------------------------
# Market refresh (every 5 min)
# ---------------------------------------------------------------------------

async def job_refresh_markets() -> None:
    """Fetch active tennis markets and rebuild WebSocket subscription."""
    global _active_markets, _ws_task

    markets = await fetch_tennis_markets()
    if not markets:
        logger.warning("job_refresh_markets: no tennis markets returned")
        return

    _active_markets = markets

    # Upsert to DB
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        for m in markets:
            await db.execute(
                pg_insert(SportMarket)
                .values(
                    condition_id=m.condition_id,
                    event_slug=m.event_slug,
                    player1=m.player1,
                    player2=m.player2,
                    token_id_p1=m.token_id_p1,
                    token_id_p2=m.token_id_p2,
                    poly_price_p1=m.price_p1,
                    poly_price_p2=m.price_p2,
                    prev_price_p1=m.price_p1,
                    last_price_update=now,
                    is_active=True,
                )
                .on_conflict_do_update(
                    index_elements=["condition_id"],
                    set_={
                        "event_slug": m.event_slug,
                        "poly_price_p1": m.price_p1,
                        "poly_price_p2": m.price_p2,
                        "last_price_update": now,
                        "is_active": True,
                    },
                )
            )
        await db.commit()

    logger.info(f"job_refresh_markets: {len(markets)} markets upserted")

    # Restart WS feed with new token list
    _restart_ws_feed()


def _restart_ws_feed() -> None:
    global _ws_task
    from app.workers.ws_feed import run_ws_feed

    if _ws_task and not _ws_task.done():
        _ws_task.cancel()

    token_ids: list[str] = []
    for m in _active_markets:
        if m.token_id_p1:
            token_ids.append(m.token_id_p1)
        if m.token_id_p2:
            token_ids.append(m.token_id_p2)

    if not token_ids:
        logger.info("WS feed: no token IDs — skipping")
        return

    loop = asyncio.get_event_loop()
    _ws_task = loop.create_task(
        run_ws_feed(token_ids, on_price_update=_on_ws_price)
    )
    logger.info(f"WS feed: restarted with {len(token_ids)} tokens")


def _on_ws_price(token_id: str, price: float) -> None:
    """Called by ws_feed on every price tick. Updates in-memory cache."""
    for m in _active_markets:
        if m.token_id_p1 == token_id:
            m.price_p1 = price
        elif m.token_id_p2 == token_id:
            m.price_p2 = price


# ---------------------------------------------------------------------------
# Price poll (every 30 s) — also writes DB and checks for big moves
# ---------------------------------------------------------------------------

async def job_poll_prices() -> None:
    """Sync prices from WS cache (or REST fallback) and check for opportunities."""
    from app.workers.ws_feed import get_all_prices, is_connected

    if not _active_markets:
        return

    ws_prices = get_all_prices()

    if not is_connected() or not ws_prices:
        # WebSocket down — fetch via REST
        token_ids = [m.token_id_p1 for m in _active_markets if m.token_id_p1]
        if token_ids:
            rest_prices = await fetch_clob_prices(token_ids)
            for m in _active_markets:
                if m.token_id_p1 in rest_prices:
                    m.price_p1 = rest_prices[m.token_id_p1]
                    m.price_p2 = round(1.0 - m.price_p1, 4)
    else:
        # Apply WS prices
        for m in _active_markets:
            if m.token_id_p1 in ws_prices:
                m.price_p1 = ws_prices[m.token_id_p1]
            if m.token_id_p2 in ws_prices:
                m.price_p2 = ws_prices[m.token_id_p2]

    # Persist + check for big moves
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        for m in _active_markets:
            result = await db.execute(
                select(SportMarket).where(SportMarket.condition_id == m.condition_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                continue

            prev = row.poly_price_p1
            row.prev_price_p1 = prev
            row.poly_price_p1 = m.price_p1
            row.poly_price_p2 = m.price_p2
            row.last_price_update = now

            # Detect significant price move
            if prev is not None and m.price_p1 is not None:
                move_pct = abs(m.price_p1 - prev) * 100
                if move_pct >= settings.min_edge_pct:
                    asyncio.get_event_loop().create_task(
                        _send_move_alert(m, prev, m.price_p1, move_pct)
                    )

        await db.commit()


async def _send_move_alert(
    market: PolyMarket,
    prev: float,
    now_price: float,
    move_pct: float,
) -> None:
    """Send a Telegram alert when a market moves significantly."""
    direction = "UP" if now_price > prev else "DOWN"
    arrow = "📈" if now_price > prev else "📉"
    msg = (
        f"{arrow} *{market.player1} vs {market.player2}*\n"
        f"Polymarket moved {direction} {move_pct:.1f}pp\n"
        f"{prev*100:.1f}% → {now_price*100:.1f}% (P1 wins)\n"
        f"[Open market]({market.url})"
    )
    try:
        from app.bot.telegram_bot import broadcast
        await broadcast(msg)
    except Exception as e:
        logger.warning(f"Alert send failed: {e}")
