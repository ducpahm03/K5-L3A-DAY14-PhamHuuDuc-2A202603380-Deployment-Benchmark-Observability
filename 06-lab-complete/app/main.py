"""
Production AI Agent — Part 6 Final Project.
"""
import datetime
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import close_redis, redis_healthy
from utils.mock_llm import ask as llm_ask


class JsonFormatter(logging.Formatter):
    """Định dạng mỗi dòng log của app thành 1 JSON object hợp lệ."""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "env": settings.environment,
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    app_logger = logging.getLogger("agent")
    app_logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    app_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    app_logger.addHandler(handler)
    app_logger.propagate = False
    return app_logger


logger = _setup_logging()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup và graceful shutdown (uvicorn xử lý SIGTERM, chờ xong in-flight rồi gọi phần sau yield)."""
    logger.info(
        "agent_startup",
        extra={
            "port": settings.port,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "redis_configured": bool(settings.redis_url),
        },
    )
    yield
    logger.info("agent_shutdown", extra={"reason": "SIGTERM_or_lifespan_exit"})
    close_redis()


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.error(
            "http_unhandled_error",
            extra={"method": request.method, "path": request.url.path, "status": 500, "ms": elapsed_ms},
        )
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "http_request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": elapsed_ms,
        },
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"field": ".".join(str(loc) for loc in err.get("loc", [])), "msg": err.get("msg", "")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "validation_error", "detail": errors},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        "internal_error",
        extra={"path": request.url.path, "error_type": type(exc).__name__},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_server_error", "detail": "An unexpected error occurred"},
    )


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[str] = Field(default=None, max_length=128)


@app.get("/health")
def health():
    """Liveness probe — luôn trả 200 khi process còn sống, kể cả khi Redis chưa sẵn sàng."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness probe — 200 khi Redis sẵn sàng, 503 khi Redis không kết nối được."""
    if not redis_healthy():
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "redis": "disconnected"},
        )
    return {"status": "ready", "redis": "connected"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)
    check_budget()

    answer = llm_ask(body.question)
    in_tokens = estimate_tokens(body.question)
    out_tokens = estimate_tokens(answer)
    cost_usd = record_cost(in_tokens, out_tokens)

    logger.info(
        "ask_completed",
        extra={
            "user_id": body.user_id,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "cost_usd": round(cost_usd, 8),
        },
    )
    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "cost_usd": round(cost_usd, 8),
    }
