"""
GhostVault Intelligence System
Pydantic v2 Schemas — request/response validation
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Address validation regexes ─────────────────────────────────────────────────
_BTC_ADDRESS_RE = re.compile(
    r"^(1[a-km-zA-HJ-NP-Z1-9]{25,34}"     # Legacy P2PKH
    r"|3[a-km-zA-HJ-NP-Z1-9]{25,34}"       # P2SH
    r"|bc1[ac-hj-np-z02-9]{6,87}"          # Bech32 / Bech32m native segwit
    r"|tb1[ac-hj-np-z02-9]{6,87})$"        # Testnet bech32
)
_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


# ── Request Schemas ────────────────────────────────────────────────────────────

class WalletAnalysisRequest(BaseModel):
    chain: str = Field(
        description="Blockchain network: bitcoin, ethereum, or solana",
        examples=["bitcoin", "ethereum", "solana"],
    )
    address: str = Field(
        description="On-chain wallet address",
        min_length=26,
        max_length=128,
    )

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in {"bitcoin", "ethereum", "solana"}:
            raise ValueError(
                f"Unsupported chain '{v}'. Supported: bitcoin, ethereum, solana"
            )
        return normalized

    @field_validator("address")
    @classmethod
    def strip_address(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def validate_address_format(self) -> "WalletAnalysisRequest":
        chain = self.chain
        address = self.address

        if chain == "bitcoin":
            if not _BTC_ADDRESS_RE.match(address):
                raise ValueError(
                    f"Invalid Bitcoin address format: '{address}'. "
                    "Expected Legacy (1...), P2SH (3...), or Bech32 (bc1...)"
                )
        elif chain == "ethereum":
            if not _ETH_ADDRESS_RE.match(address):
                raise ValueError(
                    f"Invalid Ethereum address format: '{address}'. "
                    "Expected 0x-prefixed 40-hex-char address"
                )
        elif chain == "solana":
            if not _SOL_ADDRESS_RE.match(address):
                raise ValueError(
                    f"Invalid Solana address format: '{address}'. "
                    "Expected base58-encoded 32–44 character address"
                )
        return self


# ── Internal Data Models ───────────────────────────────────────────────────────

class RawWalletMetrics(BaseModel):
    """Normalised on-chain data — chain-agnostic intermediate representation."""
    address: str
    chain: str
    first_tx_timestamp: datetime | None = None
    last_tx_timestamp: datetime | None = None
    total_tx_count: int = 0
    incoming_tx_count: int = 0
    outgoing_tx_count: int = 0
    # Average days between consecutive transactions (None if < 2 txs)
    avg_interval_days: float | None = None
    # Maximum gap between consecutive transactions
    max_gap_days: float | None = None
    # Consecutive inactivity gaps > 90 days
    long_gap_count: int = 0
    data_source: str = "rpc"


# ── Response Schemas ───────────────────────────────────────────────────────────

class WalletAnalysisResponse(BaseModel):
    # Identity
    chain: str
    address: str

    # Temporal metrics
    wallet_age_days: int | None = Field(
        default=None,
        description="Days since first recorded transaction",
    )
    days_since_last_activity: int | None = Field(
        default=None,
        description="Days since most recent transaction",
    )
    first_seen: datetime | None = None
    last_active: datetime | None = None

    # Transaction metrics
    transaction_count: int = 0
    incoming_tx_count: int = 0
    outgoing_tx_count: int = 0
    avg_tx_interval_days: float | None = None

    # Scores (0–100)
    dormancy_score: Annotated[int, Field(ge=0, le=100)] = Field(
        description="0 = highly active, 100 = fully dormant"
    )
    cold_storage_probability: Annotated[int, Field(ge=0, le=100)] = Field(
        description="Estimated probability this is a cold storage wallet"
    )

    # Classification
    risk_level: str
    wallet_type_estimate: str

    # Metadata
    analysis_timestamp: datetime
    data_source: str
    cached: bool = False

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
    components: dict[str, Any]


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    request_id: str | None = None
