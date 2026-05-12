"""
Unit tests for Pydantic v2 address validation schemas.
"""
from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "a" * 32)
os.environ.setdefault("ETHEREUM_RPC_URL", "https://mainnet.infura.io/v3/test")

from app.schemas.wallet import WalletAnalysisRequest


class TestBitcoinAddressValidation:
    def test_valid_p2pkh(self):
        req = WalletAnalysisRequest(chain="bitcoin", address="1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf")
        assert req.chain == "bitcoin"

    def test_valid_p2sh(self):
        req = WalletAnalysisRequest(chain="bitcoin", address="3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy")
        assert req.chain == "bitcoin"

    def test_valid_bech32(self):
        req = WalletAnalysisRequest(
            chain="bitcoin",
            address="bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
        )
        assert req.chain == "bitcoin"

    def test_invalid_bitcoin_address(self):
        with pytest.raises(ValidationError):
            WalletAnalysisRequest(chain="bitcoin", address="0xnotabitcoinaddress")

    def test_chain_case_insensitive(self):
        req = WalletAnalysisRequest(chain="Bitcoin", address="1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf")
        assert req.chain == "bitcoin"


class TestEthereumAddressValidation:
    def test_valid_ethereum_address(self):
        req = WalletAnalysisRequest(
            chain="ethereum",
            address="0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe",
        )
        assert req.chain == "ethereum"

    def test_invalid_no_0x_prefix(self):
        with pytest.raises(ValidationError):
            WalletAnalysisRequest(
                chain="ethereum",
                address="de0B295669a9FD93d5F28D9Ec85E40f4cb697BAe",
            )

    def test_invalid_too_short(self):
        with pytest.raises(ValidationError):
            WalletAnalysisRequest(chain="ethereum", address="0xde0B295669a9")


class TestSolanaAddressValidation:
    def test_valid_solana_address(self):
        req = WalletAnalysisRequest(
            chain="solana",
            address="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
        )
        assert req.chain == "solana"

    def test_invalid_solana_address(self):
        with pytest.raises(ValidationError):
            WalletAnalysisRequest(chain="solana", address="0xnotsolana")


class TestChainValidation:
    def test_unsupported_chain_raises(self):
        with pytest.raises(ValidationError, match="Unsupported chain"):
            WalletAnalysisRequest(chain="dogecoin", address="someaddress123")

    def test_empty_chain_raises(self):
        with pytest.raises(ValidationError):
            WalletAnalysisRequest(chain="", address="1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf")
