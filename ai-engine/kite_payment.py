"""Kite chain x402 payment interceptor for AION Yield AI engine.

Implements the HTTP 402 Payment Required flow for AI inference micropayments:
  1. Caller is about to invoke a gated tool / Claude API call
  2. pay_for_inference() sends a KITE payment on-chain to the inference provider
  3. The returned tx hash is on-chain attestation: "this AI call was paid for"
  4. The payment proof is included in every API and MCP tool response

Tiered pricing (KITE per call):
  get_vault_state              → 0.001 KITE
  get_market_context           → 0.001 KITE
  get_agent_reputation         → 0.001 KITE
  get_yield_prediction         → 0.002 KITE
  get_dca_strategies           → 0.002 KITE
  get_allocation_recommendation→ 0.005 KITE
  analyze  (Claude call)       → 0.002 KITE
  predict  (Claude call)       → 0.001 KITE

Kite Testnet:
  Chain ID : 2368
  RPC      : https://rpc.testnet.gokite.ai
  Explorer : https://testnet.kitescan.ai
  Token    : KITE (native, ETH-equivalent)
"""

import json
import os
import time
from typing import Optional

from web3 import Web3
from web3.exceptions import TransactionNotFound

from config import (
    KITE_RPC_URL,
    KITE_CHAIN_ID,
    KITE_PRIVATE_KEY,
    KITE_AGENT_ADDRESS,
    KITE_INFERENCE_PROVIDER,
)

# ── Tiered Tool Prices (KITE) ──────────────────────────────────────────────────

TOOL_PRICES: dict[str, float] = {
    # MCP tools
    "get_vault_state": 0.001,
    "get_market_context": 0.001,
    "get_agent_reputation": 0.001,
    "get_yield_prediction": 0.002,
    "get_dca_strategies": 0.002,
    "get_allocation_recommendation": 0.005,
    # FastAPI endpoints
    "analyze": 0.002,
    "predict": 0.001,
    # Generic fallback
    "ai_inference": 0.001,
}

DEFAULT_PRICE = 0.001  # fallback if action_type not in TOOL_PRICES

# ── Spending Guards ────────────────────────────────────────────────────────────

MAX_KITE_PER_TX = 0.2        # hard cap per single transaction (KITE)
MAX_KITE_PER_SESSION = 5.0   # hard cap for the running process lifetime (KITE)

_session_spent: float = 0.0  # cumulative KITE spent this session


# ── Wallet Loading ─────────────────────────────────────────────────────────────

def _load_wallet() -> tuple[str, str]:
    """Return (address, private_key) from env-vars or kite_wallet.json fallback."""
    address = KITE_AGENT_ADDRESS
    private_key = KITE_PRIVATE_KEY

    if not address or not private_key:
        wallet_path = os.path.join(os.path.dirname(__file__), "kite_wallet.json")
        try:
            with open(wallet_path) as f:
                wallet = json.load(f)
            address = wallet.get("address", "")
            private_key = wallet.get("private_key", "")
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    return address, private_key


# ── Core Payment ───────────────────────────────────────────────────────────────

def pay_for_inference(action_type: str = "ai_inference") -> dict:
    """Send a KITE micropayment for a gated tool call or Claude inference.

    The price is determined by action_type via TOOL_PRICES. Returns a
    payment-proof dict:
    {
        "paid":         True | False,
        "tx_hash":      "0x..." | None,
        "amount_kite":  0.002,
        "from":         "0x...",
        "to":           "0x...",
        "chain_id":     2368,
        "explorer_url": "https://testnet.kitescan.ai/tx/0x...",
        "action_type":  "get_yield_prediction",
        "timestamp":    1234567890,
        "error":        None | "<reason>"
    }
    """
    global _session_spent

    amount_kite = TOOL_PRICES.get(action_type, DEFAULT_PRICE)
    timestamp = int(time.time())

    base_result = {
        "paid": False,
        "tx_hash": None,
        "amount_kite": amount_kite,
        "from": None,
        "to": KITE_INFERENCE_PROVIDER,
        "chain_id": KITE_CHAIN_ID,
        "explorer_url": None,
        "action_type": action_type,
        "timestamp": timestamp,
        "error": None,
    }

    # Spending-limit guards
    if amount_kite > MAX_KITE_PER_TX:
        base_result["error"] = (
            f"Amount {amount_kite} KITE exceeds per-tx limit {MAX_KITE_PER_TX}"
        )
        return base_result

    if _session_spent + amount_kite > MAX_KITE_PER_SESSION:
        base_result["error"] = (
            f"Session spend limit reached "
            f"({_session_spent:.4f}/{MAX_KITE_PER_SESSION} KITE)"
        )
        return base_result

    address, private_key = _load_wallet()
    if not address or not private_key:
        base_result["error"] = (
            "Kite wallet not configured — set KITE_PRIVATE_KEY / KITE_AGENT_ADDRESS"
        )
        return base_result

    base_result["from"] = address

    try:
        w3 = Web3(Web3.HTTPProvider(KITE_RPC_URL, request_kwargs={"timeout": 10}))

        if not w3.is_connected():
            base_result["error"] = f"Cannot connect to Kite RPC: {KITE_RPC_URL}"
            return base_result

        nonce = w3.eth.get_transaction_count(address)
        gas_price = w3.eth.gas_price

        tx = {
            "chainId": KITE_CHAIN_ID,
            "to": Web3.to_checksum_address(KITE_INFERENCE_PROVIDER),
            "value": w3.to_wei(amount_kite, "ether"),
            "gas": 21000,
            "gasPrice": gas_price,
            "nonce": nonce,
            "data": b"",
        }

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()

        # Wait for confirmation (up to 15 s)
        receipt = None
        for _ in range(15):
            try:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
                if receipt:
                    break
            except TransactionNotFound:
                pass
            time.sleep(1)

        if receipt and receipt.status == 1:
            _session_spent += amount_kite
            explorer_url = f"https://testnet.kitescan.ai/tx/{tx_hash_hex}"
            base_result.update(
                {
                    "paid": True,
                    "tx_hash": tx_hash_hex,
                    "explorer_url": explorer_url,
                }
            )
        else:
            base_result["error"] = "Transaction sent but not confirmed (or reverted)"
            base_result["tx_hash"] = tx_hash_hex

    except Exception as exc:
        base_result["error"] = str(exc)

    return base_result


# ── Session Stats ──────────────────────────────────────────────────────────────

def get_session_spend() -> dict:
    """Return how much KITE has been spent this session."""
    return {
        "session_spent_kite": _session_spent,
        "session_limit_kite": MAX_KITE_PER_SESSION,
        "remaining_kite": max(0.0, MAX_KITE_PER_SESSION - _session_spent),
        "tool_prices": TOOL_PRICES,
    }


def get_tool_price(action_type: str) -> float:
    """Return the KITE price for a given tool/action."""
    return TOOL_PRICES.get(action_type, DEFAULT_PRICE)
