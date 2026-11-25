"""
Config — 12-Factor: mọi cấu hình đọc từ biến môi trường.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(override=False)


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    port: int = field(default_factory=lambda: _get_int("PORT", 8000))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    debug: bool = field(default_factory=lambda: _get_bool("DEBUG", False))
    agent_api_key: str = field(default_factory=lambda: os.getenv("AGENT_API_KEY", ""))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", ""))
    rate_limit_per_minute: int = field(
        default_factory=lambda: _get_int("RATE_LIMIT_PER_MINUTE", 10)
    )
    daily_budget_usd: float = field(
        default_factory=lambda: _get_float("DAILY_BUDGET_USD", 1.0)
    )
    monthly_budget_usd: float = field(
        default_factory=lambda: _get_float("MONTHLY_BUDGET_USD", 10.0)
    )


settings = Settings()
