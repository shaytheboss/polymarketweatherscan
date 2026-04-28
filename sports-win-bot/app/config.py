from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://sports:sports@localhost:5432/sports_win"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""  # comma-separated chat IDs to broadcast alerts

    # Polymarket relay (optional — for if Railway IPs get blocked by Cloudflare WAF)
    # Set POLYMARKET_RELAY_URL to your Cloudflare Worker URL to route through it.
    # Leave empty to connect to Polymarket directly (works on Railway today).
    polymarket_relay_url: str = ""

    # Opportunity thresholds
    min_edge_pct: float = 5.0        # minimum price move % to trigger alert
    min_price_change_pct: float = 3.0  # minimum WS price change to log

    # Scheduler intervals (seconds)
    market_refresh_interval: int = 300   # refresh market list from /events
    price_poll_interval: int = 30        # REST price poll when WS is down

    # App
    app_env: str = "development"
    sentry_dsn: str = ""

    @property
    def chat_id_list(self) -> List[int]:
        if not self.telegram_chat_ids:
            return []
        return [int(c.strip()) for c in self.telegram_chat_ids.split(",") if c.strip()]

    @property
    def gamma_base(self) -> str:
        relay = self.polymarket_relay_url.rstrip("/")
        return f"{relay}/gamma" if relay else "https://gamma-api.polymarket.com"

    @property
    def clob_base(self) -> str:
        relay = self.polymarket_relay_url.rstrip("/")
        return f"{relay}/clob" if relay else "https://clob.polymarket.com"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
