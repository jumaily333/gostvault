#!/usr/bin/env bash
# =============================================================================
# GhostVault Intelligence System — Example API Calls
# Run these after `make up`
# =============================================================================

BASE_URL="http://localhost:8000"

echo ""
echo "======================================"
echo " GhostVault — Example API Calls"
echo "======================================"

# ── Health Check ───────────────────────────────────────────────────────────────
echo ""
echo "[1] Health Check"
echo "----------------"
curl -s -X GET "$BASE_URL/health" | python3 -m json.tool

# ── Bitcoin Analysis — Genesis Block Address ───────────────────────────────────
echo ""
echo "[2] Bitcoin — Satoshi's Genesis Address (long dormant)"
echo "-------------------------------------------------------"
curl -s -X POST "$BASE_URL/v1/analyze-wallet" \
  -H "Content-Type: application/json" \
  -d '{
    "chain": "bitcoin",
    "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf"
  }' | python3 -m json.tool

# ── Bitcoin Analysis — Bech32 Address ─────────────────────────────────────────
echo ""
echo "[3] Bitcoin — Native SegWit (Bech32) address"
echo "---------------------------------------------"
curl -s -X POST "$BASE_URL/v1/analyze-wallet" \
  -H "Content-Type: application/json" \
  -d '{
    "chain": "bitcoin",
    "address": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
  }' | python3 -m json.tool

# ── Ethereum Analysis ──────────────────────────────────────────────────────────
echo ""
echo "[4] Ethereum — Ethereum Foundation address"
echo "------------------------------------------"
curl -s -X POST "$BASE_URL/v1/analyze-wallet" \
  -H "Content-Type: application/json" \
  -d '{
    "chain": "ethereum",
    "address": "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"
  }' | python3 -m json.tool

# ── Solana Analysis ────────────────────────────────────────────────────────────
echo ""
echo "[5] Solana — Serum DEX program address"
echo "---------------------------------------"
curl -s -X POST "$BASE_URL/v1/analyze-wallet" \
  -H "Content-Type: application/json" \
  -d '{
    "chain": "solana",
    "address": "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
  }' | python3 -m json.tool

# ── Validation Error Example ───────────────────────────────────────────────────
echo ""
echo "[6] Validation Error — invalid Ethereum address"
echo "------------------------------------------------"
curl -s -X POST "$BASE_URL/v1/analyze-wallet" \
  -H "Content-Type: application/json" \
  -d '{
    "chain": "ethereum",
    "address": "notanaddress"
  }' | python3 -m json.tool

echo ""
echo "======================================"
echo " Done"
echo "======================================"
