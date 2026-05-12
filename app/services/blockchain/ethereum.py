"""
GhostVault Intelligence System
Ethereum Service — JSON-RPC client for address analysis

Uses standard eth_* RPC methods. Compatible with:
  - Self-hosted geth / erigon / nethermind
  - Infura
  - Alchemy
  - QuickNode
  - Any EIP-1474 compliant endpoint

Note: Ethereum's eth_getTransactionCount returns the NONCE — the number of
transactions SENT from an address. To get full history (including received),
we use eth_getLogs for ERC-20 transfers and eth_getBlockByNumber for ETH
transfers. For complete accuracy a tracing node (debug_traceTransaction) or
an indexer (Etherscan API) is required. This implementation uses a multi-block
binary search approach to bound the first activity without requiring an indexer.
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

_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


class EthereumRPCClient(BaseRPCClient):
    """Ethereum-specific RPC client with hex-aware helpers."""

    def __init__(self) -> None:
        super().__init__(
            endpoint=settings.ethereum_rpc_url,
            fallback_endpoint=settings.ethereum_fallback_rpc_url,
            timeout=settings.ethereum_rpc_timeout,
            chain="ethereum",
        )

    @staticmethod
    def _hex_to_int(hex_str: str | None) -> int:
        if not hex_str:
            return 0
        return int(hex_str, 16)


class EthereumService:
    """
    Analyses an Ethereum address using standard JSON-RPC calls.

    Strategy:
    1. eth_getTransactionCount → nonce (outgoing tx count)
    2. Current block number + binary search → approximate first activity block
    3. eth_getLogs (ERC-20 Transfer events) → first/last token transfer timestamps
    4. Block timestamps for first/last blocks with activity
    """

    def __init__(self) -> None:
        self._rpc = EthereumRPCClient()

    async def close(self) -> None:
        await self._rpc.close()

    async def _get_block_timestamp(self, block_number: int) -> datetime | None:
        try:
            block = await self._rpc.call(
                "eth_getBlockByNumber",
                [hex(block_number), False],
                cache_ttl=3600,  # Block data is immutable — cache aggressively
            )
            if block and "timestamp" in block:
                ts = EthereumRPCClient._hex_to_int(block["timestamp"])
                return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception as exc:
            logger.warning("eth_block_timestamp_failed", block=block_number, error=str(exc))
        return None

    async def _get_current_block(self) -> int:
        result = await self._rpc.call("eth_blockNumber", [], cache_ttl=30)
        return EthereumRPCClient._hex_to_int(result)

    async def _binary_search_first_activity_block(
        self,
        address: str,
        low: int,
        high: int,
    ) -> int | None:
        """
        Binary search to find the approximate first block where address had
        a non-zero nonce or balance. Reduces O(n) block scan to O(log n).
        """
        address_lower = address.lower()
        first_active_block: int | None = None

        iterations = 0
        while low <= high and iterations < 20:
            iterations += 1
            mid = (low + high) // 2
            try:
                nonce_hex = await self._rpc.call(
                    "eth_getTransactionCount",
                    [address_lower, hex(mid)],
                    cache_ttl=3600,
                )
                nonce_at_mid = EthereumRPCClient._hex_to_int(nonce_hex)
            except Exception:
                break

            if nonce_at_mid > 0:
                first_active_block = mid
                high = mid - 1
            else:
                low = mid + 1

        return first_active_block

    async def get_wallet_metrics(self, address: str) -> RawWalletMetrics:
        logger.info("eth_analysis_start", address=address[:12] + "...")

        address_lower = address.lower()

        # ── 1. Current nonce = number of sent transactions ─────────────────────
        nonce_hex = await self._rpc.call(
            "eth_getTransactionCount",
            [address_lower, "latest"],
            cache_ttl=60,
        )
        outgoing_tx_count = EthereumRPCClient._hex_to_int(nonce_hex)

        # ── 2. Current block ───────────────────────────────────────────────────
        current_block = await self._get_current_block()
        last_block_timestamp = await self._get_block_timestamp(current_block)

        # ── 3. ERC-20 Transfer events (incoming and outgoing) ─────────────────
        # eth_getLogs is limited to ~10k results per call; use block ranges
        # We query the most recent 200k blocks (~3–4 years) as a bounded window.
        scan_from = max(0, current_block - 200_000)

        incoming_logs: list[dict[str, Any]] = []
        outgoing_logs: list[dict[str, Any]] = []

        # Pad address to 32 bytes for topic matching
        padded_address = "0x" + address_lower[2:].zfill(64)

        try:
            incoming_logs = await self._rpc.call(
                "eth_getLogs",
                [{
                    "fromBlock": hex(scan_from),
                    "toBlock": "latest",
                    "topics": [_TRANSFER_TOPIC, None, padded_address],
                }],
                cache_ttl=120,
            ) or []
        except Exception as exc:
            logger.warning("eth_incoming_logs_failed", error=str(exc))

        try:
            outgoing_logs = await self._rpc.call(
                "eth_getLogs",
                [{
                    "fromBlock": hex(scan_from),
                    "toBlock": "latest",
                    "topics": [_TRANSFER_TOPIC, padded_address, None],
                }],
                cache_ttl=120,
            ) or []
        except Exception as exc:
            logger.warning("eth_outgoing_logs_failed", error=str(exc))

        all_logs = incoming_logs + outgoing_logs
        incoming_token_count = len(incoming_logs)
        # outgoing_tx_count already covers ETH sends; token outgoing additive
        total_tx_count = outgoing_tx_count + len(all_logs)

        # ── 4. Resolve timestamps from log block numbers ───────────────────────
        first_tx_timestamp: datetime | None = None
        last_tx_timestamp: datetime | None = None
        tx_timestamps: list[datetime] = []

        if all_logs:
            block_numbers_raw = sorted(
                {EthereumRPCClient._hex_to_int(log.get("blockNumber", "0x0")) for log in all_logs
                 if log.get("blockNumber")}
            )
            # Fetch timestamps for first, last, and a sample of blocks
            sample_blocks = set()
            sample_blocks.add(block_numbers_raw[0])
            sample_blocks.add(block_numbers_raw[-1])
            step = max(1, len(block_numbers_raw) // 15)
            for bn in block_numbers_raw[::step]:
                sample_blocks.add(bn)

            ts_results = await asyncio.gather(
                *[self._get_block_timestamp(bn) for bn in sorted(sample_blocks)],
                return_exceptions=True,
            )
            for ts in ts_results:
                if isinstance(ts, datetime):
                    tx_timestamps.append(ts)

            tx_timestamps.sort()

        # ── 5. Binary search for first outgoing transaction block ──────────────
        if outgoing_tx_count > 0 and not tx_timestamps:
            first_block = await self._binary_search_first_activity_block(
                address_lower, 0, current_block
            )
            if first_block is not None:
                ts = await self._get_block_timestamp(first_block)
                if ts:
                    tx_timestamps.append(ts)

        tx_timestamps.sort()
        if tx_timestamps:
            first_tx_timestamp = tx_timestamps[0]
            last_tx_timestamp = tx_timestamps[-1]

        # ── 6. Interval statistics ─────────────────────────────────────────────
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

        return RawWalletMetrics(
            address=address,
            chain="ethereum",
            first_tx_timestamp=first_tx_timestamp,
            last_tx_timestamp=last_tx_timestamp,
            total_tx_count=max(total_tx_count, outgoing_tx_count),
            incoming_tx_count=incoming_token_count,
            outgoing_tx_count=outgoing_tx_count,
            avg_interval_days=avg_interval_days,
            max_gap_days=max_gap_days,
            long_gap_count=long_gap_count,
            data_source="ethereum-rpc",
        )
