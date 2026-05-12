"""
GhostVault Intelligence System
Health Check — /health endpoint
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.db.cache import get_redis
from app.db.session import engine
from app.schemas.wallet import HealthResponse

logger = get_logger(__name__)

router = APIRouter(tags=["System"])


@router.get(
    "/health",
    summary="Service health check",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def health_check() -> JSONResponse:
    components: dict[str, object] = {}
    overall_healthy = True

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        components["postgresql"] = {"status": "ok"}
    except Exception as exc:
        components["postgresql"] = {"status": "error", "detail": str(exc)}
        overall_healthy = False

    # ── Redis ──────────────────────────────────────────────────────────────────
    try:
        redis = await get_redis()
        await redis.ping()
        components["redis"] = {"status": "ok"}
    except Exception as exc:
        components["redis"] = {"status": "error", "detail": str(exc)}
        overall_healthy = False

    payload = {
        "status": "healthy" if overall_healthy else "degraded",
        "version": "1.0.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "components": components,
    }

    http_status = status.HTTP_200_OK if overall_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=http_status)
