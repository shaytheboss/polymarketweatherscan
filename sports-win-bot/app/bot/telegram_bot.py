"""
Telegram bot — polling mode (no webhook, works on Railway).
"""
from __future__ import annotations

import logging

from telegram import Bot
from telegram.ext import Application, CommandHandler

from app.config import settings
from app.bot.handlers import cmd_start, cmd_markets, cmd_test

logger = logging.getLogger(__name__)

_app: Application | None = None


def get_application() -> Application | None:
    return _app


async def build_application() -> Application | None:
    global _app
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")
        return None

    _app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .build()
    )
    _app.add_handler(CommandHandler("start", cmd_start))
    _app.add_handler(CommandHandler("markets", cmd_markets))
    _app.add_handler(CommandHandler("test", cmd_test))

    return _app


async def broadcast(message: str) -> None:
    """Send a message to all configured chat IDs."""
    chat_ids = settings.chat_id_list
    if not chat_ids:
        logger.debug("broadcast: no chat IDs configured")
        return
    if not settings.telegram_bot_token:
        return

    bot = Bot(token=settings.telegram_bot_token)
    async with bot:
        for chat_id in chat_ids:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"Telegram send to {chat_id} failed: {e}")
