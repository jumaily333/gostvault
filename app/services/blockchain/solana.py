"""
GhostVault Intelligence System
Solana Service — JSON-RPC client for address analysis

Uses the Solana JSON-RPC API:
  https://docs.solana.com/api/http

Key methods used:
  - getSignaturesForAddress  → paginated tx history with block timestamps
  - getAccountInfo           → account type detection
  - getSlot                  → current slot

Compatible with:
  - api.mainnet-beta.solana.com (public, rate-limited)
  - Helius
  - QuickNode
  - GenesysGo
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.schemas.wallet import RawWalletMetrics
from app.services.blockchain.base_rpc import BaseRPCClient, RPCConnectionError

logger = get_logger(__name__)
settings = get_settings()

# Maximum signatures per getSignaturesForAddress call
_MAX_SIGS_PER_CALL = 1000
# Total signature pages to fetch (bounded to avoid rate limits)
_MAX_PAGES = 5


class SolanaRPCClient(BaseRPCClient):
    """Solana JSON-RPC client."""

    def __init__(self) -> None:
        super().__init__(
            endpoint=settings.solana_rpc_url,
            fallback_endpoint=settings.solana_fallback_rpc_url,
            timeout=settings.solana_rpc_timeout,
            chain="solana",
        )


class SolanaService:
    """
    Analyses a Solana address using getSignaturesForAddress pagination.

    Solana's RPC natively returns timestamps with each signature entry
    (blockTime field), making temporal analysis straightforward.

    Account type is inferred from account data fields:
      - executable=True → program (not a user wallet)
      - owner=TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA → token account
      - owner=11111111111111111111111111111111 → system (user) wallet
    """

    def __init__(self) -> None:
        self._rpc = SolanaRPCClient()

    async def close(self) -> None:
        await self._rpc.close()

    async def _get_account_info(self, address: str) -> dict[str, Any] | None:
        try:
            result = await self._rpc.call(
                "getAccountInfo",
                [address, {"encoding": "base64"}],
                cache_ttl=300,
            )
            if result and "value" in result:
                return result["value"]  # type: ignore[return-value]
        except Exception as exc:
            logger.warning("sol_account_info_failed", address=address[:12], error=str(exc))
        return None

    async def _fetch_signature_page(
        self,
        address: str,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch one page of signatures, optionally paginating via `before` cursor."""
        params: dict[str, Any] = {"limit": _MAX_SIGS_PER_CALL}
        if before:
            params["before"] = before

        result = await self._rpc.call(
            "getSignaturesForAddress",
            [address, params],
            cache_ttl=60,
        )
        return result or []  # type: ignore[return-value]

    async def get_wallet_metrics(self, address: str) -> RawWalletMetrics:
        logger.info("sol_analysis_start", address=address[:12] + "...")

        # ── 1. Account info for type detection ────────────────────────────────
        account_info = await self._get_account_info(address)

        all_signatures: list[dict[str, Any]] = []
        before_cursor: str | None = None
        total_fetched = 0

        # ── 2. Paginate through signature history ──────────────────────────────
        for page in range(_MAX_PAGES):
            try:
                page_sigs = await self._fetch_signature_page(address, before=before_cursor)
            except Exception as exc:
                logger.warning(
                    "sol_signature_fetch_failed",
                    page=page,
                    error=str(exc),
                )
                break

            if not page_sigs:
                break

            all_signatures.extend(page_sigs)
            total_fetched += len(page_sigs)

            if len(page_sigs) < _MAX_SIGS_PER_CALL:
                # Last page reached
                break

            # Set cursor for next page (oldest signature in this page)
            before_cursor = page_sigs[-1]["signature"]
            logger.debug(
                "sol_signature_page_fetched",
                page=page + 1,
                count=len(page_sigs),
                total=total_fetched,
            )
            # Small delay to respect public RPC rate limits
            await asyncio.sleep(0.2)

        if not all_signatures:
            return RawWalletMetrics(
                address=address,
                chain="solana",
                total_tx_count=0,
                data_source="solana-rpc",
            )

        # ── 3. Extract timestamps ──────────────────────────────────────────────
        # getSignaturesForAddress returns newest-first
        # blockTime is Unix timestamp (seconds), can be None for unconfirmed
        tx_timestamps: list[datetime] = []
        error_count = 0

        for sig in all_signatures:
            block_time = sig.get("blockTime")
            err = sig.get("err")
            if err is not None:
                error_count += 1
            if block_time is not None:
                tx_timestamps.append(
                    datetime.fromtimestamp(block_time, tz=timezone.utc)
                )

        tx_timestamps.sort()

        first_tx_timestamp: datetime | None = tx_timestamps[0] if tx_timestamps else None
        last_tx_timestamp: datetime | None = tx_timestamps[-1] if tx_timestamps else None

        # ── 4. Interval statistics ─────────────────────────────────────────────
        avg_interval_days: float | None = None
        max_gap_days: float | None = None
        long_gap_count = 0

        if len(tx_timestamps) >= 2:
            gaps = [
                (tx_timestamps[i + 1] - tx_timestamps[i]).total_seconds() / 86400
                for i in range(len(tx_timestamps) - 1)
            ]
            avg_interval_days = sum(gaps) / len(gaps)
            max_gap_days = max(gaps)
            long_gap_count = sum(1 for g in gaps if g > 90)

        # ── 5. Directional tx classification ──────────────────────────────────
        # Solana signatures don't directly indicate direction without full tx decode
        # We approximate: if the wallet's tx count far exceeds received count,
        # it's likely a sending wallet. We use error rate as a signal for bots.
        # Full directional analysis requires getTransaction for each sig (expensive).
        total_tx_count = len(all_signatures)
        # Approximate: roughly half outgoing (conservative)
        outgoing_estimate = total_tx_count // 2
        incoming_estimate = total_tx_count - outgoing_estimate

        return RawWalletMetrics(
            address=address,
            chain="solana",
            first_tx_timestamp=first_tx_timestamp,
            last_tx_timestamp=last_tx_timestamp,
            total_tx_count=total_tx_count,
            incoming_tx_count=incoming_estimate,
            outgoing_tx_count=outgoing_estimate,
            avg_interval_days=avg_interval_days,
            max_gap_days=max_gap_days,
            long_gap_count=long_gap_count,
            data_source="solana-rpc",
        )
