"""
GhostVault Intelligence System
Cache — async Redis client with structured key management
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)
settings = get_settings()

_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return _redis_pool


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None


class CacheKey:
    PREFIX = "ghostvault"

    @classmethod
    def wallet_analysis(cls, chain: str, address: str) -> str:
        return f"{cls.PREFIX}:wallet:{chain}:{address.lower()}"

    @classmethod
    def rpc_raw(cls, chain: str, method: str, params_hash: str) -> str:
        return f"{cls.PREFIX}:rpc:{chain}:{method}:{params_hash}"


async def cache_get(key: str) -> dict[str, Any] | None:
    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception as exc:
        logger.warning("cache_get_failed", key=key, error=str(exc))
        return None


async def cache_set(
    key: str,
    value: dict[str, Any],
    ttl: int | None = None,
) -> None:
    try:
        redis = await get_redis()
        payload = json.dumps(value, default=str)
        effective_ttl = ttl if ttl is not None else settings.cache_ttl_seconds
        await redis.setex(key, effective_ttl, payload)
    except Exception as exc:
        logger.warning("cache_set_failed", key=key, error=str(exc))


async def cache_delete(key: str) -> None:
    try:
        redis = await get_redis()
        await redis.delete(key)
    except Exception as exc:
        logger.warning("cache_delete_failed", key=key, error=str(exc))
