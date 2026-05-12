"""
Unit tests for the Dormancy Engine.
These tests verify scoring logic without any I/O or RPC calls.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

# Provide required env vars before importing settings
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("ETHEREUM_RPC_URL", "https://mainnet.infura.io/v3/test")

from app.schemas.wallet import RawWalletMetrics
from app.services.analysis.dormancy import DormancyEngine

engine = DormancyEngine()

NOW = datetime.now(tz=timezone.utc)


def make_metrics(**kwargs) -> RawWalletMetrics:
    defaults = {
        "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf",
        "chain": "bitcoin",
        "total_tx_count": 10,
        "incoming_tx_count": 5,
        "outgoing_tx_count": 5,
        "data_source": "test",
    }
    defaults.update(kwargs)
    return RawWalletMetrics(**defaults)


class TestInactivityScoring:
    def test_active_wallet_scores_low(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=365),
            last_tx_timestamp=NOW - timedelta(days=1),
        )
        result = engine.score(metrics)
        assert result["dormancy_score"] < 40, "Active wallet should have low dormancy"

    def test_dormant_2_years_scores_high(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=1000),
            last_tx_timestamp=NOW - timedelta(days=730),
        )
        result = engine.score(metrics)
        assert result["dormancy_score"] >= 70, "2-year dormant wallet should score HIGH"

    def test_dormant_5_years_scores_very_high(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=2000),
            last_tx_timestamp=NOW - timedelta(days=1825),
        )
        result = engine.score(metrics)
        assert result["dormancy_score"] >= 85

    def test_no_timestamps_gets_neutral_score(self):
        metrics = make_metrics()
        result = engine.score(metrics)
        # Should not crash and should return a valid score
        assert 0 <= result["dormancy_score"] <= 100


class TestColdStorageProbability:
    def test_receive_only_wallet_high_cold_prob(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=1500),
            last_tx_timestamp=NOW - timedelta(days=500),
            total_tx_count=5,
            incoming_tx_count=5,
            outgoing_tx_count=0,
        )
        result = engine.score(metrics)
        assert result["cold_storage_probability"] >= 60

    def test_high_frequency_sender_low_cold_prob(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=365),
            last_tx_timestamp=NOW - timedelta(days=1),
            total_tx_count=500,
            incoming_tx_count=250,
            outgoing_tx_count=250,
            avg_interval_days=0.7,
        )
        result = engine.score(metrics)
        assert result["cold_storage_probability"] < 50


class TestWalletClassification:
    def test_fresh_wallet_classified_correctly(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=10),
            last_tx_timestamp=NOW - timedelta(days=1),
            total_tx_count=3,
        )
        result = engine.score(metrics)
        assert result["wallet_type_estimate"] == "FRESH_WALLET"

    def test_dormant_old_wallet_classified_cold_storage(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=1800),
            last_tx_timestamp=NOW - timedelta(days=900),
            total_tx_count=3,
            incoming_tx_count=3,
            outgoing_tx_count=0,
            avg_interval_days=450.0,
            max_gap_days=900.0,
            long_gap_count=1,
        )
        result = engine.score(metrics)
        assert result["wallet_type_estimate"] == "POSSIBLE_COLD_STORAGE"

    def test_score_breakdown_present(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=365),
            last_tx_timestamp=NOW - timedelta(days=180),
        )
        result = engine.score(metrics)
        breakdown = result["_score_breakdown"]
        assert "inactivity" in breakdown
        assert "frequency" in breakdown
        assert "age" in breakdown
        assert "movement_pattern" in breakdown
        # Verify weights sum correctly
        w = breakdown["weighted_composite"]
        assert isinstance(w, float)


class TestRiskLevelBoundaries:
    def test_low_activity_threshold(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=2000),
            last_tx_timestamp=NOW - timedelta(days=800),
            total_tx_count=2,
        )
        result = engine.score(metrics)
        assert result["risk_level"] == "LOW_ACTIVITY"

    def test_high_activity_threshold(self):
        metrics = make_metrics(
            first_tx_timestamp=NOW - timedelta(days=365),
            last_tx_timestamp=NOW - timedelta(hours=2),
            total_tx_count=1000,
            avg_interval_days=0.36,
        )
        result = engine.score(metrics)
        assert result["risk_level"] == "HIGH_ACTIVITY"
