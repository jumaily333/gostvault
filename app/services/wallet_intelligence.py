"""
GhostVault Intelligence System
Wallet Intelligence Orchestrator

Coordinates:
  1. Chain-specific RPC data fetching
  2. Dormancy engine scoring
  3. Cache read/write
  4. Database persistence
  5. Structured logging
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.cache import CacheKey, cache_get, cache_set
from app.models.wallet import WalletAnalysis
from app.schemas.wallet import RawWalletMetrics, WalletAnalysisRequest, WalletAnalysisResponse
from app.services.analysis.dormancy import DormancyEngine
from app.services.blockchain.bitcoin import BitcoinService
from app.services.blockchain.base_rpc import RPCConnectionError, RPCError
from app.services.blockchain.ethereum import EthereumService
from app.services.blockchain.solana import SolanaService

logger = get_logger(__name__)

_dormancy_engine = DormancyEngine()

# Service singletons — created once and reused across requests
_bitcoin_service: BitcoinService | None = None
_ethereum_service: EthereumService | None = None
_solana_service: SolanaService | None = None


def _get_bitcoin_service() -> BitcoinService:
    global _bitcoin_service
    if _bitcoin_service is None:
        _bitcoin_service = BitcoinService()
    return _bitcoin_service


def _get_ethereum_service() -> EthereumService:
    global _ethereum_service
    if _ethereum_service is None:
        _ethereum_service = EthereumService()
    return _ethereum_service


def _get_solana_service() -> SolanaService:
    global _solana_service
    if _solana_service is None:
        _solana_service = SolanaService()
    return _solana_service


async def shutdown_services() -> None:
    """Called during application shutdown to cleanly close RPC connections."""
    if _bitcoin_service:
        await _bitcoin_service.close()
    if _ethereum_service:
        await _ethereum_service.close()
    if _solana_service:
        await _solana_service.close()


async def _fetch_metrics(request: WalletAnalysisRequest) -> RawWalletMetrics:
    """Dispatch to the correct chain service."""
    chain = request.chain
    address = request.address

    if chain == "bitcoin":
        return await _get_bitcoin_service().get_wallet_metrics(address)
    elif chain == "ethereum":
        return await _get_ethereum_service().get_wallet_metrics(address)
    elif chain == "solana":
        return await _get_solana_service().get_wallet_metrics(address)
    else:
        raise ValueError(f"Unsupported chain: {chain}")


async def _persist_analysis(
    session: AsyncSession,
    request: WalletAnalysisRequest,
    scores: dict[str, Any],
    raw_metrics: RawWalletMetrics,
) -> None:
    """Upsert the analysis result into PostgreSQL."""
    from sqlalchemy import select

    # Check for existing record to update rather than insert
    stmt = select(WalletAnalysis).where(
        WalletAnalysis.chain == request.chain,
        WalletAnalysis.address == request.address,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.wallet_age_days = scores["wallet_age_days"]
        existing.days_since_last_activity = scores["days_since_last_activity"]
        existing.first_seen_at = scores["first_seen"]
        existing.last_active_at = scores["last_active"]
        existing.transaction_count = scores["transaction_count"]
        existing.incoming_tx_count = scores["incoming_tx_count"]
        existing.outgoing_tx_count = scores["outgoing_tx_count"]
        existing.avg_tx_interval_days = scores["avg_tx_interval_days"]
        existing.dormancy_score = scores["dormancy_score"]
        existing.cold_storage_probability = scores["cold_storage_probability"]
        existing.risk_level = scores["risk_level"]
        existing.wallet_type_estimate = scores["wallet_type_estimate"]
        existing.raw_metrics = {
            "score_breakdown": scores.get("_score_breakdown"),
            "data_source": raw_metrics.data_source,
            "long_gap_count": raw_metrics.long_gap_count,
            "max_gap_days": raw_metrics.max_gap_days,
        }
    else:
        record = WalletAnalysis(
            chain=request.chain,
            address=request.address,
            wallet_age_days=scores["wallet_age_days"],
            days_since_last_activity=scores["days_since_last_activity"],
            first_seen_at=scores["first_seen"],
            last_active_at=scores["last_active"],
            transaction_count=scores["transaction_count"],
            incoming_tx_count=scores["incoming_tx_count"],
            outgoing_tx_count=scores["outgoing_tx_count"],
            avg_tx_interval_days=scores["avg_tx_interval_days"],
            dormancy_score=scores["dormancy_score"],
            cold_storage_probability=scores["cold_storage_probability"],
            risk_level=scores["risk_level"],
            wallet_type_estimate=scores["wallet_type_estimate"],
            raw_metrics={
                "score_breakdown": scores.get("_score_breakdown"),
                "data_source": raw_metrics.data_source,
                "long_gap_count": raw_metrics.long_gap_count,
                "max_gap_days": raw_metrics.max_gap_days,
            },
        )
        session.add(record)


async def analyze_wallet(
    request: WalletAnalysisRequest,
    db_session: AsyncSession,
) -> WalletAnalysisResponse:
    request_id = str(uuid.uuid4())[:8]

    bound_logger = logger.bind(
        request_id=request_id,
        chain=request.chain,
        address=request.address[:12] + "...",
    )
    bound_logger.info("wallet_analysis_started")

    # ── 1. Cache lookup ────────────────────────────────────────────────────────
    cache_key = CacheKey.wallet_analysis(request.chain, request.address)
    cached_result = await cache_get(cache_key)
    if cached_result:
        bound_logger.info("wallet_analysis_cache_hit")
        cached_result["cached"] = True
        return WalletAnalysisResponse(**cached_result)

    # ── 2. Fetch on-chain data ────────────────────────────────────────────────
    try:
        raw_metrics = await _fetch_metrics(request)
        bound_logger.info(
            "wallet_metrics_fetched",
            tx_count=raw_metrics.total_tx_count,
            data_source=raw_metrics.data_source,
        )
    except (RPCConnectionError, RPCError) as exc:
        bound_logger.error("rpc_error", error=str(exc))
        raise
    except ValueError as exc:
        bound_logger.error("invalid_address", error=str(exc))
        raise

    # ── 3. Score ───────────────────────────────────────────────────────────────
    scores = _dormancy_engine.score(raw_metrics)
    bound_logger.info(
        "wallet_scored",
        dormancy_score=scores["dormancy_score"],
        cold_storage_probability=scores["cold_storage_probability"],
        risk_level=scores["risk_level"],
        wallet_type=scores["wallet_type_estimate"],
    )

    # ── 4. Persist ─────────────────────────────────────────────────────────────
    try:
        await _persist_analysis(db_session, request, scores, raw_metrics)
    except Exception as exc:
        # Non-fatal — log but don't fail the response
        bound_logger.error("db_persist_failed", error=str(exc))

    # ── 5. Build response ──────────────────────────────────────────────────────
    analysis_timestamp = datetime.now(tz=timezone.utc)

    response = WalletAnalysisResponse(
        chain=request.chain,
        address=request.address,
        wallet_age_days=scores["wallet_age_days"],
        days_since_last_activity=scores["days_since_last_activity"],
        first_seen=scores["first_seen"],
        last_active=scores["last_active"],
        transaction_count=scores["transaction_count"],
        incoming_tx_count=scores["incoming_tx_count"],
        outgoing_tx_count=scores["outgoing_tx_count"],
        avg_tx_interval_days=scores["avg_tx_interval_days"],
        dormancy_score=scores["dormancy_score"],
        cold_storage_probability=scores["cold_storage_probability"],
        risk_level=scores["risk_level"],
        wallet_type_estimate=scores["wallet_type_estimate"],
        analysis_timestamp=analysis_timestamp,
        data_source=raw_metrics.data_source,
        cached=False,
    )

    # ── 6. Cache result ────────────────────────────────────────────────────────
    response_dict = response.model_dump(mode="json")
    await cache_set(cache_key, response_dict)

    bound_logger.info("wallet_analysis_complete")
    return response
