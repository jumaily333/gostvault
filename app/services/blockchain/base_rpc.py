"""
GhostVault Intelligence System
RPC Abstraction Layer — base async HTTP JSON-RPC client with retry
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import get_logger
from app.db.cache import CacheKey, cache_get, cache_set

logger = get_logger(__name__)


class RPCError(Exception):
    """Raised when the RPC node returns an error payload."""
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"RPC error {code}: {message}")


class RPCConnectionError(Exception):
    """Raised when the RPC node is unreachable or times out."""


class BaseRPCClient:
    """
    Async JSON-RPC 2.0 client.

    Subclass this for each chain, overriding `_endpoint_url` and
    `_auth_headers` if authentication is required.
    """

    def __init__(
        self,
        endpoint: str,
        timeout: int = 30,
        chain: str = "unknown",
        fallback_endpoint: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._fallback_endpoint = fallback_endpoint
        self._timeout = timeout
        self._chain = chain
        self._client: httpx.AsyncClient | None = None
        self._call_id = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=float(self._timeout),
                    write=10.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                    keepalive_expiry=30,
                ),
                http2=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _next_id(self) -> int:
        self._call_id += 1
        return self._call_id

    def _params_hash(self, method: str, params: list[Any] | dict[str, Any]) -> str:
        raw = json.dumps({"method": method, "params": params}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=False,
    )
    async def _http_post(
        self, endpoint: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning(
                "rpc_transport_error",
                chain=self._chain,
                endpoint=endpoint,
                error=str(exc),
            )
            raise
        except httpx.HTTPStatusError as exc:
            raise RPCConnectionError(
                f"HTTP {exc.response.status_code} from {endpoint}"
            ) from exc

    async def call(
        self,
        method: str,
        params: list[Any] | dict[str, Any] | None = None,
        cache_ttl: int | None = None,
    ) -> Any:
        """Execute an RPC method call, with optional Redis caching."""
        if params is None:
            params = []

        params_hash = self._params_hash(method, params)
        cache_key = CacheKey.rpc_raw(self._chain, method, params_hash)

        if cache_ttl is not None and cache_ttl > 0:
            cached = await cache_get(cache_key)
            if cached is not None:
                logger.debug("rpc_cache_hit", chain=self._chain, method=method)
                return cached.get("result")

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._next_id(),
        }

        try:
            data = await self._http_post(self._endpoint, payload)
        except (RetryError, RPCConnectionError) as primary_exc:
            if self._fallback_endpoint:
                logger.warning(
                    "rpc_primary_failed_using_fallback",
                    chain=self._chain,
                    method=method,
                    error=str(primary_exc),
                )
                try:
                    data = await self._http_post(self._fallback_endpoint, payload)
                except Exception as fallback_exc:
                    raise RPCConnectionError(
                        f"Both primary and fallback RPC failed for {self._chain}:{method}. "
                        f"Primary: {primary_exc}. Fallback: {fallback_exc}"
                    ) from fallback_exc
            else:
                raise RPCConnectionError(
                    f"RPC call failed after retries: {self._chain}:{method}"
                ) from primary_exc

        if "error" in data and data["error"] is not None:
            err = data["error"]
            raise RPCError(
                code=err.get("code", -1),
                message=err.get("message", "Unknown RPC error"),
            )

        result = data.get("result")

        if cache_ttl is not None and cache_ttl > 0 and result is not None:
            await cache_set(cache_key, {"result": result}, ttl=cache_ttl)

        return result
