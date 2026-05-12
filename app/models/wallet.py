"""
GhostVault Intelligence System
ORM Models — wallet analysis persistence
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Chain(str, enum.Enum):
    BITCOIN = "bitcoin"
    ETHEREUM = "ethereum"
    SOLANA = "solana"


class RiskLevel(str, enum.Enum):
    LOW_ACTIVITY = "LOW_ACTIVITY"
    MODERATE_ACTIVITY = "MODERATE_ACTIVITY"
    HIGH_ACTIVITY = "HIGH_ACTIVITY"
    UNKNOWN = "UNKNOWN"


class WalletTypeEstimate(str, enum.Enum):
    POSSIBLE_COLD_STORAGE = "POSSIBLE_COLD_STORAGE"
    LIKELY_HOT_WALLET = "LIKELY_HOT_WALLET"
    EXCHANGE_WALLET = "EXCHANGE_WALLET"
    WHALE_WALLET = "WHALE_WALLET"
    FRESH_WALLET = "FRESH_WALLET"
    UNKNOWN = "UNKNOWN"


class WalletAnalysis(Base):
    __tablename__ = "wallet_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    chain: Mapped[str] = mapped_column(
        Enum(Chain, name="chain_enum"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    # Temporal metrics
    wallet_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_since_last_activity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Transaction metrics
    transaction_count: Mapped[int] = mapped_column(BigInteger, default=0)
    avg_tx_interval_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    incoming_tx_count: Mapped[int] = mapped_column(BigInteger, default=0)
    outgoing_tx_count: Mapped[int] = mapped_column(BigInteger, default=0)

    # Scoring
    dormancy_score: Mapped[int] = mapped_column(Integer, nullable=False)
    cold_storage_probability: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(
        Enum(RiskLevel, name="risk_level_enum"), nullable=False
    )
    wallet_type_estimate: Mapped[str] = mapped_column(
        Enum(WalletTypeEstimate, name="wallet_type_enum"), nullable=False
    )

    # Raw data snapshot for auditability
    raw_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Error tracking
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<WalletAnalysis chain={self.chain} address={self.address[:8]}...>"
