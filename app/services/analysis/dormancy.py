"""
GhostVault Intelligence System
Dormancy Engine — multi-factor scoring for wallet classification

Scoring Architecture
--------------------
Four independent sub-scores are computed (each 0–100) then weighted
to produce a final dormancy_score:

  Sub-score 1: Inactivity Score  (weight: 0.40)
    Measures how long the wallet has been silent.
    Uses a logistic curve centred at the DORMANCY_THRESHOLD_DAYS (default 365).
    A wallet idle for 365 days scores ~75. Idle for 730 days scores ~95.

  Sub-score 2: Transaction Frequency Score  (weight: 0.25)
    Low tx frequency increases dormancy probability.
    Uses average days between transactions on a log scale.

  Sub-score 3: Wallet Age Score  (weight: 0.20)
    Older wallets are more likely to be cold storage.
    A wallet created > 5 years ago asymptotically approaches 100.

  Sub-score 4: Movement Pattern Score  (weight: 0.15)
    Long inactivity gaps relative to wallet history signal cold storage.
    Ratio of (max_gap / wallet_age) is the primary signal.

Cold Storage Probability
------------------------
Combines dormancy_score with directional transaction pattern.
Wallets that receive but rarely send are strong cold storage candidates.

Risk Level Classification
-------------------------
  LOW_ACTIVITY     dormancy_score >= 70
  MODERATE_ACTIVITY  40 <= dormancy_score < 70
  HIGH_ACTIVITY    dormancy_score < 40

Wallet Type Estimation
----------------------
  POSSIBLE_COLD_STORAGE  high dormancy + low outgoing ratio + old
  WHALE_WALLET           very high tx count OR large movement intervals
  EXCHANGE_WALLET        very high tx count + frequent activity
  LIKELY_HOT_WALLET      low dormancy + high outgoing ratio
  FRESH_WALLET           wallet age < 30 days
  UNKNOWN                insufficient data
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.core.settings import get_settings
from app.models.wallet import RiskLevel, WalletTypeEstimate
from app.schemas.wallet import RawWalletMetrics

settings = get_settings()


def _logistic(
    x: float,
    midpoint: float,
    steepness: float = 0.008,
    max_score: float = 100.0,
) -> float:
    """
    Sigmoid function: maps x to [0, max_score].
    Returns ~50 at x=midpoint, approaches max_score as x→∞.
    """
    return max_score / (1.0 + math.exp(-steepness * (x - midpoint)))


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _inactivity_score(days_since_last_activity: float | None) -> float:
    """
    Returns 0 for a wallet active today.
    Returns ~50 for a wallet idle 180 days.
    Returns ~75 for idle 365 days (1 year).
    Returns ~95 for idle 730 days (2 years).
    Returns 100 for idle >= 1825 days (5 years).
    """
    if days_since_last_activity is None:
        return 50.0  # Unknown — neutral prior

    if days_since_last_activity <= 0:
        return 0.0

    threshold = float(settings.dormancy_threshold_days)
    score = _logistic(days_since_last_activity, midpoint=threshold * 0.7, steepness=0.007)
    return _clamp(score)


def _frequency_score(
    avg_interval_days: float | None,
    total_tx_count: int,
) -> float:
    """
    Low frequency = high dormancy score.
    A wallet that transacts every 365 days on average scores ~70.
    A wallet that transacts daily scores ~5.
    A wallet with 0–1 transactions scores 90 (insufficient activity data).
    """
    if total_tx_count <= 1:
        return 90.0  # Single tx or none → strong dormancy signal

    if avg_interval_days is None:
        return 50.0  # Unknown

    if avg_interval_days <= 0:
        return 0.0

    # Log scale: avg_interval 1d→5, 30d→40, 180d→65, 365d→75, 1000d→90
    raw = math.log1p(avg_interval_days) / math.log1p(1000) * 90
    return _clamp(raw)


def _age_score(wallet_age_days: float | None) -> float:
    """
    Older wallets are more likely to be cold storage.
    < 30 days  → 5   (fresh, unlikely cold storage)
    1 year     → 40
    3 years    → 70
    5 years    → 85
    10+ years  → 98
    """
    if wallet_age_days is None:
        return 30.0  # Unknown — slight lean toward non-dormant

    if wallet_age_days < 30:
        return 5.0

    score = _logistic(wallet_age_days, midpoint=365 * 3, steepness=0.003)
    return _clamp(score)


def _movement_pattern_score(
    max_gap_days: float | None,
    wallet_age_days: float | None,
    long_gap_count: int,
) -> float:
    """
    High maximum gap relative to wallet lifetime signals cold-storage style usage.
    Having many long gaps also raises the score.
    """
    if max_gap_days is None or wallet_age_days is None or wallet_age_days <= 0:
        return 30.0  # Insufficient data

    gap_ratio = max_gap_days / max(wallet_age_days, 1)

    # Gap ratio of 0.8+ means most of wallet's life was inactive
    gap_score = _clamp(gap_ratio * 100)

    # Bonus for multiple long gaps (indicates recurring cold-storage-style deposits)
    gap_count_bonus = min(long_gap_count * 5, 20)

    return _clamp(gap_score + gap_count_bonus)


def _cold_storage_probability(
    dormancy_score: float,
    outgoing_tx_count: int,
    incoming_tx_count: int,
    wallet_age_days: float | None,
    total_tx_count: int,
) -> int:
    """
    Cold storage wallets:
      - Have high dormancy scores
      - Receive more than they send (or only receive)
      - Are older wallets
      - Have low overall tx count

    Formula uses dormancy_score as the base, then adjusts:
      +20 if outgoing ratio is < 10%
      +10 if wallet is older than 2 years and dormancy > 60
      -15 if tx count > WHALE_TX_THRESHOLD (exchange/active wallet)
    """
    base = dormancy_score

    total = total_tx_count or 1
    outgoing_ratio = outgoing_tx_count / total

    if outgoing_ratio < 0.10:
        base += 20
    elif outgoing_ratio < 0.25:
        base += 10
    elif outgoing_ratio > 0.75:
        base -= 15

    if wallet_age_days and wallet_age_days > 730 and dormancy_score > 60:
        base += 10

    if total_tx_count > settings.whale_tx_threshold:
        base -= 15

    return int(_clamp(base))


def _classify_risk_level(dormancy_score: float) -> RiskLevel:
    if dormancy_score >= 70:
        return RiskLevel.LOW_ACTIVITY
    elif dormancy_score >= 40:
        return RiskLevel.MODERATE_ACTIVITY
    else:
        return RiskLevel.HIGH_ACTIVITY


def _classify_wallet_type(
    dormancy_score: float,
    cold_storage_prob: int,
    total_tx_count: int,
    wallet_age_days: float | None,
    outgoing_tx_count: int,
    incoming_tx_count: int,
) -> WalletTypeEstimate:
    total = total_tx_count or 1
    outgoing_ratio = outgoing_tx_count / total

    # Fresh wallet
    if wallet_age_days is not None and wallet_age_days < 30:
        return WalletTypeEstimate.FRESH_WALLET

    # Exchange / high-frequency wallet
    if total_tx_count > settings.whale_tx_threshold * 5 and dormancy_score < 40:
        return WalletTypeEstimate.EXCHANGE_WALLET

    # Whale wallet (high volume, less frequent)
    if total_tx_count > settings.whale_tx_threshold and dormancy_score >= 30:
        return WalletTypeEstimate.WHALE_WALLET

    # Cold storage candidate
    if cold_storage_prob >= 65 and dormancy_score >= 60:
        return WalletTypeEstimate.POSSIBLE_COLD_STORAGE

    # Hot wallet (active, sending)
    if dormancy_score < 40 and outgoing_ratio > 0.40:
        return WalletTypeEstimate.LIKELY_HOT_WALLET

    # Cold storage with moderate confidence
    if cold_storage_prob >= 50 and dormancy_score >= 50:
        return WalletTypeEstimate.POSSIBLE_COLD_STORAGE

    return WalletTypeEstimate.UNKNOWN


class DormancyEngine:
    """
    Orchestrates all sub-scores and produces the final analytics report.
    All scoring is deterministic given the same input metrics.
    """

    def __init__(self) -> None:
        self._w_inactivity = settings.score_weight_inactivity
        self._w_frequency = settings.score_weight_tx_frequency
        self._w_age = settings.score_weight_wallet_age
        self._w_movement = settings.score_weight_movement_pattern

    def score(self, metrics: RawWalletMetrics) -> dict:
        now = datetime.now(tz=timezone.utc)

        # ── Derived temporal features ──────────────────────────────────────────
        wallet_age_days: float | None = None
        days_since_last_activity: float | None = None

        if metrics.first_tx_timestamp:
            wallet_age_days = (now - metrics.first_tx_timestamp).total_seconds() / 86400

        if metrics.last_tx_timestamp:
            days_since_last_activity = (
                now - metrics.last_tx_timestamp
            ).total_seconds() / 86400

        # ── Sub-scores ────────────────────────────────────────────────────────
        s_inactivity = _inactivity_score(days_since_last_activity)
        s_frequency = _frequency_score(
            metrics.avg_interval_days, metrics.total_tx_count
        )
        s_age = _age_score(wallet_age_days)
        s_movement = _movement_pattern_score(
            metrics.max_gap_days, wallet_age_days, metrics.long_gap_count
        )

        # ── Weighted composite dormancy score ─────────────────────────────────
        dormancy_score_raw = (
            s_inactivity * self._w_inactivity
            + s_frequency * self._w_frequency
            + s_age * self._w_age
            + s_movement * self._w_movement
        )
        dormancy_score = int(_clamp(dormancy_score_raw))

        # ── Cold storage probability ───────────────────────────────────────────
        cold_prob = _cold_storage_probability(
            dormancy_score_raw,
            metrics.outgoing_tx_count,
            metrics.incoming_tx_count,
            wallet_age_days,
            metrics.total_tx_count,
        )

        # ── Classification ────────────────────────────────────────────────────
        risk_level = _classify_risk_level(dormancy_score_raw)
        wallet_type = _classify_wallet_type(
            dormancy_score_raw,
            cold_prob,
            metrics.total_tx_count,
            wallet_age_days,
            metrics.outgoing_tx_count,
            metrics.incoming_tx_count,
        )

        return {
            "wallet_age_days": int(wallet_age_days) if wallet_age_days is not None else None,
            "days_since_last_activity": (
                int(days_since_last_activity) if days_since_last_activity is not None else None
            ),
            "first_seen": metrics.first_tx_timestamp,
            "last_active": metrics.last_tx_timestamp,
            "transaction_count": metrics.total_tx_count,
            "incoming_tx_count": metrics.incoming_tx_count,
            "outgoing_tx_count": metrics.outgoing_tx_count,
            "avg_tx_interval_days": (
                round(metrics.avg_interval_days, 2)
                if metrics.avg_interval_days is not None
                else None
            ),
            "dormancy_score": dormancy_score,
            "cold_storage_probability": cold_prob,
            "risk_level": risk_level.value,
            "wallet_type_estimate": wallet_type.value,
            # Debug breakdown (useful for tuning and auditing)
            "_score_breakdown": {
                "inactivity": round(s_inactivity, 2),
                "frequency": round(s_frequency, 2),
                "age": round(s_age, 2),
                "movement_pattern": round(s_movement, 2),
                "weighted_composite": round(dormancy_score_raw, 2),
            },
        }
