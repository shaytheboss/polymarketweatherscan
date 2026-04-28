"""
Telegram command handlers.

Commands:
  /start   — welcome message
  /markets — list active tennis markets with Polymarket links + current odds
  /test    — test Polymarket API connectivity
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown(
        "*Sports Win Probability Bot*\n\n"
        "I monitor live Polymarket tennis markets and alert you when odds shift.\n\n"
        "*Commands:*\n"
        "/markets — show active tennis markets\n"
        "/test — check Polymarket API connection\n"
    )


async def cmd_markets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all active tennis markets with current odds."""
    from app.workers.jobs import _active_markets

    if not _active_markets:
        await update.message.reply_text(
            "No active tennis markets cached yet. Try again in a minute."
        )
        return

    lines = [f"*Active Tennis Markets* ({len(_active_markets)})\n"]
    for m in _active_markets[:20]:  # cap at 20 to avoid Telegram message limit
        p1_pct = m.price_p1 * 100
        p2_pct = m.price_p2 * 100
        lines.append(
            f"• [{m.player1} vs {m.player2}]({m.url})\n"
            f"  {m.player1}: {p1_pct:.1f}%  |  {m.player2}: {p2_pct:.1f}%"
        )

    await update.message.reply_markdown(
        "\n".join(lines),
        disable_web_page_preview=True,
    )


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Test Polymarket API connectivity and show diagnostic info."""
    await update.message.reply_text("Testing Polymarket connection...")

    from app.collectors.polymarket import test_connectivity
    from app.workers.ws_feed import is_connected

    result = await test_connectivity()
    ws_status = "connected" if is_connected() else "disconnected"

    lines = [
        "*Polymarket Connectivity Test*\n",
        f"Relay: {result['relay_url']}",
        f"Endpoint: `{result['gamma_endpoint']}`",
        f"Status: {'OK' if result['ok'] else 'FAILED'}",
        f"Markets found: {result['markets_found']}",
        f"WebSocket: {ws_status}",
    ]
    if result.get("sample"):
        lines.append(f"Sample: {result['sample']}")
    if not result["ok"] and result.get("http_status"):
        lines.append(f"Error: {result['http_status']}")
        lines.append(
            "\n*If you're seeing 403 errors:*\n"
            "Deploy `cloudflare-worker.js` (in parent repo) and set "
            "`POLYMARKET_RELAY_URL` in Railway env vars."
        )

    await update.message.reply_markdown(
        "\n".join(lines),
        disable_web_page_preview=True,
    )
