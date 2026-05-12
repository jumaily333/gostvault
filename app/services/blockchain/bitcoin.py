"""
GhostVault Intelligence System
Bitcoin Service — Electrum protocol client for address history

Uses the Electrum JSON-RPC protocol over SSL (port 50002) or TCP (50001).
This connects directly to any Electrum-compatible server (Blockstream,
self-hosted Fulcrum, ElectrumX, etc.) without requiring a full Bitcoin node.

Protocol reference: https://electrumx.readthedocs.io/en/latest/protocol-methods.html
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
import struct
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.schemas.wallet import RawWalletMetrics
from app.services.blockchain.base_rpc import RPCConnectionError, RPCError

logger = get_logger(__name__)
settings = get_settings()


def _script_hash_from_address(address: str) -> str:
    """
    Convert a Bitcoin address to the Electrum script_hash format.
    Electrum uses the SHA256 hash of the scriptPubKey, reversed (little-endian).

    Supports:
      - P2PKH  (1...)
      - P2SH   (3...)
      - P2WPKH (bc1q...)
      - P2TR   (bc1p...)
    """
    import base58  # type: ignore[import]

    # ── Bech32 / Bech32m (native segwit) ──────────────────────────────────────
    if address.startswith(("bc1", "tb1")):
        # Decode bech32: witness program extraction
        # We build the scriptPubKey directly
        hrp, data_part = address.split("1", 1)
        # Bech32 charset
        _CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
        decoded = [_CHARSET.index(c) for c in data_part]
        # Drop checksum (last 6 chars), convert from 5-bit groups
        decoded = decoded[:-6]
        # Version is first element; remaining are 5-bit data
        version = decoded[0]
        data5 = decoded[1:]

        # Convert 5-bit groups to 8-bit bytes
        acc = 0
        bits = 0
        result = []
        for value in data5:
            acc = ((acc << 5) | value) & 0xFFFFFFFF
            bits += 5
            if bits >= 8:
                bits -= 8
                result.append((acc >> bits) & 0xFF)
        witness_program = bytes(result)

        if version == 0 and len(witness_program) == 20:
            # P2WPKH
            script = bytes([0x00, 0x14]) + witness_program
        elif version == 0 and len(witness_program) == 32:
            # P2WSH
            script = bytes([0x00, 0x20]) + witness_program
        elif version == 1 and len(witness_program) == 32:
            # P2TR (Taproot)
            script = bytes([0x51, 0x20]) + witness_program
        else:
            raise ValueError(
                f"Unsupported segwit version {version} or program length {len(witness_program)}"
            )
    else:
        # ── Legacy Base58Check ─────────────────────────────────────────────────
        decoded_bytes = base58.b58decode_check(address)
        version_byte = decoded_bytes[0]
        payload = decoded_bytes[1:]

        if version_byte in (0x00, 0x6F):  # Mainnet / Testnet P2PKH
            script = (
                bytes([0x76, 0xA9, 0x14])  # OP_DUP OP_HASH160 PUSH20
                + payload
                + bytes([0x88, 0xAC])       # OP_EQUALVERIFY OP_CHECKSIG
            )
        elif version_byte in (0x05, 0xC4):  # Mainnet / Testnet P2SH
            script = (
                bytes([0xA9, 0x14])  # OP_HASH160 PUSH20
                + payload
                + bytes([0x87])       # OP_EQUAL
            )
        else:
            raise ValueError(
                f"Unknown Bitcoin address version byte: 0x{version_byte:02X}"
            )

    script_hash = hashlib.sha256(script).digest()
    return script_hash[::-1].hex()  # little-endian (Electrum convention)


class ElectrumClient:
    """
    Async Electrum JSON-RPC client over persistent TCP/SSL connection.
    Handles request/response correlation, reconnection, and timeouts.
    """

    def __init__(
        self,
        host: str,
        port: int,
        use_ssl: bool = True,
        timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._use_ssl = use_ssl
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._call_id = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        ssl_ctx: ssl.SSLContext | None = None
        if self._use_ssl:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE  # Many electrum servers use self-signed certs

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=ssl_ctx),
            timeout=self._timeout,
        )
        logger.info(
            "electrum_connected",
            host=self._host,
            port=self._port,
            ssl=self._use_ssl,
        )

    async def disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    async def _ensure_connected(self) -> None:
        if self._writer is None or self._writer.is_closing():
            await self.connect()

    async def call(self, method: str, params: list[Any]) -> Any:
        async with self._lock:
            await self._ensure_connected()
            self._call_id += 1
            request = json.dumps({
                "id": self._call_id,
                "method": method,
                "params": params,
            }) + "\n"

            assert self._writer is not None
            assert self._reader is not None

            self._writer.write(request.encode())
            await self._writer.drain()

            raw = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self._timeout,
            )
            if not raw:
                raise RPCConnectionError("Electrum server closed the connection")

            data = json.loads(raw.decode().strip())
            if "error" in data and data["error"]:
                err = data["error"]
                raise RPCError(
                    code=err.get("code", -1),
                    message=err.get("message", "Electrum error"),
                )
            return data.get("result")


class BitcoinService:
    """
    Fetches Bitcoin wallet history from an Electrum-compatible server
    and normalises it into RawWalletMetrics.
    """

    def __init__(self) -> None:
        self._electrum = ElectrumClient(
            host=settings.electrum_host,
            port=settings.electrum_port,
            use_ssl=settings.electrum_use_ssl,
            timeout=settings.bitcoin_rpc_timeout,
        )

    async def close(self) -> None:
        await self._electrum.disconnect()

    async def get_wallet_metrics(self, address: str) -> RawWalletMetrics:
        logger.info("btc_analysis_start", address=address[:12] + "...")

        try:
            script_hash = _script_hash_from_address(address)
        except Exception as exc:
            raise ValueError(f"Cannot derive script hash from address: {exc}") from exc

        # Fetch transaction history — list of {tx_hash, height}
        history: list[dict[str, Any]] = await self._electrum.call(
            "blockchain.scripthash.get_history", [script_hash]
        )

        if not history:
            return RawWalletMetrics(
                address=address,
                chain="bitcoin",
                total_tx_count=0,
                data_source="electrum",
            )

        total_tx_count = len(history)

        # Fetch block headers to resolve timestamps for first and last tx
        # Sort by height; height=0 means unconfirmed (mempool)
        confirmed = [tx for tx in history if tx.get("height", 0) > 0]
        confirmed.sort(key=lambda x: x["height"])

        first_tx_timestamp: datetime | None = None
        last_tx_timestamp: datetime | None = None
        tx_timestamps: list[datetime] = []

        heights_to_fetch = set()
        if confirmed:
            heights_to_fetch.add(confirmed[0]["height"])
            heights_to_fetch.add(confirmed[-1]["height"])

            # Sample up to 20 intermediate heights for interval analysis
            if len(confirmed) > 2:
                step = max(1, len(confirmed) // 20)
                for tx in confirmed[::step]:
                    heights_to_fetch.add(tx["height"])

        for height in sorted(heights_to_fetch):
            try:
                header_hex: str = await self._electrum.call(
                    "blockchain.block.header", [height]
                )
                # Bitcoin block header is 80 bytes; timestamp at offset 68 (4 bytes LE)
                header_bytes = bytes.fromhex(header_hex)
                timestamp_unix = struct.unpack_from("<I", header_bytes, 68)[0]
                ts = datetime.fromtimestamp(timestamp_unix, tz=timezone.utc)
                tx_timestamps.append(ts)
            except Exception as exc:
                logger.warning("btc_header_fetch_failed", height=height, error=str(exc))

        tx_timestamps.sort()
        if tx_timestamps:
            first_tx_timestamp = tx_timestamps[0]
            last_tx_timestamp = tx_timestamps[-1]

        # Compute interval statistics
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

        # Bitcoin transactions are either incoming or outgoing.
        # Without fetching full tx data, we approximate directional count
        # by fetching the utxo set (presence = received funds).
        # For a full breakdown we'd need blockchain.transaction.get for each tx.
        # We keep RPC calls bounded: fetch directional info only for the
        # most recent 50 transactions.
        incoming = 0
        outgoing = 0
        sample = confirmed[-50:]
        for tx_entry in sample:
            try:
                tx_detail: dict[str, Any] = await self._electrum.call(
                    "blockchain.transaction.get", [tx_entry["tx_hash"], True]
                )
                # Check if our scripthash is in vout (incoming) or vin (outgoing)
                for vout in tx_detail.get("vout", []):
                    spk = vout.get("scriptPubKey", {})
                    if address in spk.get("addresses", []) or address == spk.get("address", ""):
                        incoming += 1
                        break
                for vin in tx_detail.get("vin", []):
                    if vin.get("scriptSig") or vin.get("txinwitness"):
                        outgoing += 1
                        break
            except Exception:
                pass  # Best-effort; non-fatal

        return RawWalletMetrics(
            address=address,
            chain="bitcoin",
            first_tx_timestamp=first_tx_timestamp,
            last_tx_timestamp=last_tx_timestamp,
            total_tx_count=total_tx_count,
            incoming_tx_count=incoming,
            outgoing_tx_count=outgoing,
            avg_interval_days=avg_interval_days,
            max_gap_days=max_gap_days,
            long_gap_count=long_gap_count,
            data_source="electrum",
        )
