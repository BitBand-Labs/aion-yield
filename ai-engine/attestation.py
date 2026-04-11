"""On-chain attestation poster for AION Yield AI engine.

After each AI inference call, this module records a tamper-proof attestation
on-chain via AIAttestationRegistry.sol.  The attestation binds:
  - which AI agent ran the inference
  - what action was performed ("analyze", "predict", …)
  - a keccak256 hash of the inference output (output cannot be altered later)
  - the Kite x402 tx hash that paid for the inference

If the registry contract address is not configured, the function silently
returns None — the API response is never blocked by attestation availability.
"""

import asyncio
import hashlib
import json
import os
from typing import Optional

from web3 import Web3

from config import get_chain_config

# ── AIAttestationRegistry ABI (only the functions we need) ────────────────────

_REGISTRY_ABI = json.loads("""[
    {
        "inputs": [
            {"name": "agentId",    "type": "address"},
            {"name": "actionType", "type": "string"},
            {"name": "outputHash", "type": "bytes32"},
            {"name": "kitePayTx",  "type": "string"}
        ],
        "name": "recordAttestation",
        "outputs": [{"name": "id", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "attestationCount",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]""")


def _hash_output(output: dict) -> bytes:
    """Return a 32-byte keccak256 digest of the JSON-encoded inference output."""
    raw = json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha3_256(raw).digest()  # web3.py expects bytes32 as bytes


def _load_reporter_wallet() -> tuple[str, str]:
    """Return (address, private_key) of the attestation reporter from env."""
    # Reuse the Kite wallet for signing — it's the same AI agent identity
    from kite_payment import _load_wallet
    return _load_wallet()


async def post_attestation(
    action_type: str,
    output: dict,
    kite_pay_tx: str,
    chain: str = "fuji",
) -> Optional[int]:
    """Write an AI inference attestation to AIAttestationRegistry on-chain.

    Returns the attestation ID (uint256) on success, or None if skipped/failed.
    This is always called fire-and-forget — failures are suppressed so the
    API response is never blocked.
    """
    try:
        return await asyncio.get_event_loop().run_in_executor(
            None,
            _post_attestation_sync,
            action_type,
            output,
            kite_pay_tx,
            chain,
        )
    except Exception:
        return None


def _post_attestation_sync(
    action_type: str,
    output: dict,
    kite_pay_tx: str,
    chain: str,
) -> Optional[int]:
    cfg = get_chain_config(chain)
    registry_addr = cfg.get("ai_attestation_registry")

    if not registry_addr:
        # Not yet deployed / configured — silently skip
        return None

    address, private_key = _load_reporter_wallet()
    if not address or not private_key:
        return None

    try:
        w3 = Web3(Web3.HTTPProvider(cfg["rpc_url"]))
        registry = w3.eth.contract(
            address=Web3.to_checksum_address(registry_addr),
            abi=_REGISTRY_ABI,
        )

        output_hash = _hash_output(output)  # bytes32

        tx = registry.functions.recordAttestation(
            Web3.to_checksum_address(address),
            action_type,
            output_hash,
            kite_pay_tx or "",
        ).build_transaction({
            "from": Web3.to_checksum_address(address),
            "nonce": w3.eth.get_transaction_count(address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        if receipt.status == 1:
            # Decode the returned attestation ID from the receipt logs
            # (simpler: just read attestationCount - 1)
            count = registry.functions.attestationCount().call()
            return count - 1

    except Exception:
        pass

    return None
