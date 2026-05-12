"""
GhostVault Intelligence System
Core Settings — loaded from environment / .env file
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "production"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_workers: int = 4
    secret_key: str = Field(min_length=32)

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300

    # ── Bitcoin RPC ────────────────────────────────────────────────────────────
    bitcoin_rpc_url: str = ""
    bitcoin_rpc_timeout: int = 30
    electrum_host: str = "electrum.blockstream.info"
    electrum_port: int = 50002
    electrum_use_ssl: bool = True

    # ── Ethereum RPC ───────────────────────────────────────────────────────────
    ethereum_rpc_url: str
    ethereum_rpc_timeout: int = 30
    ethereum_fallback_rpc_url: str = ""

    # ── Solana RPC ─────────────────────────────────────────────────────────────
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_rpc_timeout: int = 30
    solana_fallback_rpc_url: str = ""

    # ── Dormancy Engine Weights ────────────────────────────────────────────────
    dormancy_threshold_days: int = 365
    whale_tx_threshold: int = 100
    score_weight_inactivity: float = 0.40
    score_weight_tx_frequency: float = 0.25
    score_weight_wallet_age: float = 0.20
    score_weight_movement_pattern: float = 0.15

    # ── Logging ────────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    @model_validator(mode="after")
    def validate_score_weights(self) -> "Settings":
        total = (
            self.score_weight_inactivity
            + self.score_weight_tx_frequency
            + self.score_weight_wallet_age
            + self.score_weight_movement_pattern
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"Score weights must sum to 1.0, got {total:.4f}. "
                "Check SCORE_WEIGHT_* environment variables."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
