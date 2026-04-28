"""
Polymarket sports market collector.

Correct approach (validated from tennis-scanner repo running on Railway):
  - Discovery endpoint: GET /events?tag_id=864   (NOT /markets?tag_slug=tennis)
  - Market URL:  https://polymarket.com/event/{event.slug}  (event slug, NOT market slug)
  - CLOB prices: GET /prices?token_id=t1,t2,...   (batch REST fallback)
  - Real-time:   WebSocket wss://ws-subscriptions-clob.polymarket.com/ws/market

Tennis tag_id = 864 (hardcoded — no discovery needed).

IP blocking note:
  Polymarket blocks cloud hosting IPs via Cloudflare WAF in some regions.
  If you hit 403 errors, set POLYMARKET_RELAY_URL to a Cloudflare Worker that
  proxies /gamma/* → gamma-api.polymarket.com and /clob/* → clob.polymarket.com.
  The cloudflare-worker.js in the parent repo can be deployed in ~2 minutes.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

TENNIS_TAG_ID = 864

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://polymarket.com/",
    "Origin": "https://polymarket.com",
}


@dataclass
class PolyMarket:
    """One Polymarket sports market (a single match win/loss binary)."""
    condition_id: str
    event_slug: str        # used in market URL: polymarket.com/event/{slug}
    player1: str           # YES outcome — player1 wins
    player2: str           # NO outcome  — player2 wins
    token_id_p1: str       # CLOB token for player1 YES
    token_id_p2: str       # CLOB token for player2 YES
    price_p1: float = 0.5  # P(player1 wins) — real-time from CLOB
    price_p2: float = 0.5
    question: str = ""

    @property
    def url(self) -> str:
        if self.event_slug:
            return f"https://polymarket.com/event/{self.event_slug}"
        return "https://polymarket.com"

    @property
    def implied_edge(self) -> float:
        """How far from 50/50 the market is — larger = more skewed."""
        return abs(self.price_p1 - 0.5) * 100


def _gamma_url(path: str) -> str:
    from app.config import settings
    return f"{settings.gamma_base}{path}"


def _clob_url(path: str) -> str:
    from app.config import settings
    return f"{settings.clob_base}{path}"


async def fetch_tennis_markets() -> list[PolyMarket]:
    """
    Fetch active tennis markets from Polymarket.

    Uses /events?tag_id=864 — the correct endpoint confirmed working from Railway.
    The response is a list of event objects, each containing a 'markets' array.
    """
    url = _gamma_url("/events")
    params = {
        "tag_id": TENNIS_TAG_ID,
        "active": "true",
        "closed": "false",
        "limit": 100,
    }

    try:
        async with aiohttp.ClientSession(headers=_HEADERS) as session:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
    except aiohttp.ClientResponseError as e:
        logger.error(f"Polymarket /events HTTP {e.status}: {e.message}")
        return []
    except Exception as e:
        logger.error(f"Polymarket /events error: {type(e).__name__}: {e}")
        return []

    # Response is either a list of events, or {"data": [...]}
    events = data if isinstance(data, list) else data.get("data", [])
    markets: list[PolyMarket] = []

    for event in events:
        event_slug = event.get("slug", "")

        for item in event.get("markets", []):
            try:
                # Parse token IDs (stored as JSON string in Gamma API response)
                raw_tokens = item.get("clobTokenIds", "[]")
                token_ids: list = (
                    json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
                )
                if len(token_ids) < 2:
                    continue

                # Parse current prices (also JSON string)
                raw_prices = item.get("outcomePrices", "[0.5,0.5]")
                prices: list = (
                    json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                )

                player1 = item.get("homeTeam") or ""
                player2 = item.get("awayTeam") or ""

                # Skip markets where we can't identify players
                if not player1 and not player2:
                    continue

                markets.append(
                    PolyMarket(
                        condition_id=item.get("conditionId", ""),
                        event_slug=event_slug,
                        player1=player1,
                        player2=player2,
                        token_id_p1=str(token_ids[0]),
                        token_id_p2=str(token_ids[1]),
                        price_p1=float(prices[0]) if prices else 0.5,
                        price_p2=float(prices[1]) if len(prices) > 1 else 0.5,
                        question=item.get("question", ""),
                    )
                )
            except Exception as e:
                logger.debug(f"Skipping market item: {e}")
                continue

    logger.info(
        f"Polymarket: {len(markets)} tennis markets found "
        f"(from {len(events)} events, endpoint: {url})"
    )
    return markets


async def fetch_clob_prices(token_ids: list[str]) -> dict[str, float]:
    """
    Batch-fetch current mid prices for multiple token IDs via CLOB REST.
    Returns {token_id: price_float}.  Used as fallback when WebSocket is down.
    """
    if not token_ids:
        return {}
    try:
        async with aiohttp.ClientSession(headers=_HEADERS) as session:
            async with session.get(
                _clob_url("/prices"),
                params={"token_id": ",".join(token_ids)},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        return {k: float(v) for k, v in data.items() if v is not None}
    except Exception as e:
        logger.warning(f"CLOB /prices error: {type(e).__name__}: {e}")
        return {}


async def test_connectivity() -> dict:
    """Diagnostic — call from /health or a bot command."""
    from app.config import settings
    result: dict = {
        "relay_url": settings.polymarket_relay_url or "not set (direct)",
        "gamma_endpoint": _gamma_url("/events"),
        "ok": False,
        "http_status": None,
        "events_found": 0,
        "markets_found": 0,
        "sample": None,
    }
    try:
        markets = await fetch_tennis_markets()
        result["ok"] = True
        result["events_found"] = len(markets)
        result["markets_found"] = len(markets)
        if markets:
            m = markets[0]
            result["sample"] = f"{m.player1} vs {m.player2} @ {m.price_p1:.2f} ({m.url})"
    except Exception as e:
        result["http_status"] = str(e)
    return result
