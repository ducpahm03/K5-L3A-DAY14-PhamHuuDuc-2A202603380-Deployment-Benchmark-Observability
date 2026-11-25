"""
Cost guard (tiêu chí 8).

Cộng dồn chi phí LLM theo token vào Redis (theo ngày và theo tháng).
Vượt DAILY_BUDGET_USD hoặc MONTHLY_BUDGET_USD -> HTTP 402.
"""
import datetime
import logging
import threading

from fastapi import HTTPException, status

from app.config import settings
from app.redis_client import r

logger = logging.getLogger("agent.cost_guard")

# Giá USD / 1K tokens (tham khảo gpt-4o-mini)
PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006

DAILY_TTL_SECONDS = 60 * 60 * 48
MONTHLY_TTL_SECONDS = 60 * 60 * 24 * 35

_local_lock = threading.Lock()
_local_spend: dict[str, float] = {}


def _keys() -> tuple[str, str]:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (
        f"cost:daily:{now.strftime('%Y-%m-%d')}",
        f"cost:monthly:{now.strftime('%Y-%m')}",
    )


def estimate_tokens(text: str) -> int:
    """Ước lượng token đơn giản (~4 ký tự / token, tối thiểu 1)."""
    return max(1, (len(text) + 3) // 4)


def _get_spend(redis_key: str) -> float:
    if r is not None:
        try:
            val = r.get(redis_key)
            return float(val) if val is not None else 0.0
        except Exception as exc:
            logger.warning("cost_guard_redis_read_fallback", extra={"error": str(exc)})
    with _local_lock:
        return _local_spend.get(redis_key, 0.0)


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget -> raise HTTPException(status_code=402)."""
    daily_key, monthly_key = _keys()
    daily_spent = _get_spend(daily_key)
    monthly_spent = _get_spend(monthly_key)

    if daily_spent >= settings.daily_budget_usd:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Daily budget limit exceeded",
        )
    if monthly_spent >= settings.monthly_budget_usd:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Monthly budget limit exceeded",
        )


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí vào Redis, trả về chi phí request (USD)."""
    cost = (
        (input_tokens / 1000.0) * PRICE_INPUT_PER_1K
        + (output_tokens / 1000.0) * PRICE_OUTPUT_PER_1K
    )
    daily_key, monthly_key = _keys()

    if r is not None:
        try:
            pipe = r.pipeline()
            pipe.incrbyfloat(daily_key, cost)
            pipe.expire(daily_key, DAILY_TTL_SECONDS)
            pipe.incrbyfloat(monthly_key, cost)
            pipe.expire(monthly_key, MONTHLY_TTL_SECONDS)
            pipe.execute()
            return cost
        except Exception as exc:
            logger.warning("cost_guard_redis_write_fallback", extra={"error": str(exc)})

    with _local_lock:
        _local_spend[daily_key] = _local_spend.get(daily_key, 0.0) + cost
        _local_spend[monthly_key] = _local_spend.get(monthly_key, 0.0) + cost
    return cost
