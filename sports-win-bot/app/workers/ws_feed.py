"""
Real-time Polymarket CLOB price feed via WebSocket.

Protocol:
  1. Connect to wss://ws-subscriptions-clob.polymarket.com/ws/market
  2. Send {"type": "market", "assets_ids": [token_id_1, token_id_2, ...]}
  3. Send "PING" every 10 s, expect "PONG" back
  4. Receive price messages: {"asset_id": "...", "price": "0.65"}
     or  {"asset_id": "...", "best_bid": "0.64", "best_ask": "0.66"}

Runs as a long-lived asyncio task managed by scheduler.py.
Reconnects automatically on disconnect.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_INTERVAL = 10   # seconds between "PING" keepalives
RECONNECT_DELAY = 5  # seconds to wait before reconnecting

# Shared in-memory price store: {token_id: price}
_prices: dict[str, float] = {}
_connected: bool = False


def get_price(token_id: str) -> float | None:
    return _prices.get(token_id)


def get_all_prices() -> dict[str, float]:
    return dict(_prices)


def is_connected() -> bool:
    return _connected


def _handle_message(raw: str, on_update: Callable | None) -> None:
    """Parse one WebSocket message and update _prices."""
    global _prices

    if raw == "PONG":
        return

    try:
        data = json.loads(raw)
    except Exception:
        return

    # Server sometimes sends a list of price updates
    items = data if isinstance(data, list) else [data]

    for msg in items:
        asset_id = msg.get("asset_id") or msg.get("token_id")
        if not asset_id:
            continue

        price: float | None = None
        if "price" in msg:
            price = float(msg["price"])
        elif "best_bid" in msg and "best_ask" in msg:
            bid = float(msg["best_bid"])
            ask = float(msg["best_ask"])
            price = (bid + ask) / 2.0

        if price is not None:
            _prices[asset_id] = price
            if on_update:
                try:
                    on_update(asset_id, price)
                except Exception as e:
                    logger.debug(f"on_update callback error: {e}")


async def run_ws_feed(
    token_ids: list[str],
    on_price_update: Callable | None = None,
) -> None:
    """
    Connect to the CLOB WebSocket and stream prices for token_ids.
    Runs forever, reconnecting on disconnect.  Cancel the task to stop.
    """
    global _connected

    if not token_ids:
        logger.warning("WS feed: no token IDs — not starting")
        return

    logger.info(f"WS feed: starting for {len(token_ids)} tokens")

    while True:
        _connected = False
        try:
            async with websockets.connect(
                WS_URL,
                ping_interval=None,   # we handle pings manually
                open_timeout=15,
                close_timeout=5,
            ) as ws:
                # Subscribe to all tokens in one message
                await ws.send(json.dumps({
                    "type": "market",
                    "assets_ids": token_ids,
                }))
                _connected = True
                logger.info(f"WS feed: connected and subscribed to {len(token_ids)} tokens")

                # Keepalive ping task
                async def _ping_loop():
                    while True:
                        await asyncio.sleep(PING_INTERVAL)
                        try:
                            await ws.send("PING")
                        except Exception:
                            break

                ping_task = asyncio.create_task(_ping_loop())
                try:
                    async for raw in ws:
                        _handle_message(raw, on_price_update)
                finally:
                    ping_task.cancel()

        except asyncio.CancelledError:
            logger.info("WS feed: cancelled")
            _connected = False
            return
        except Exception as e:
            _connected = False
            logger.warning(f"WS feed: disconnected ({type(e).__name__}: {e}). Reconnecting in {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)
