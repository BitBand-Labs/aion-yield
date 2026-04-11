import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SEPOLIA_RPC_URL = os.getenv("SEPOLIA_RPC_URL", "https://eth-sepolia.g.alchemy.com/v2/demo")
AVAX_FUJI_RPC_URL = os.getenv("AVAX_FUJI_RPC_URL", "https://api.avax-test.network/ext/bc/C/rpc")

# Kite Chain (testnet) — x402 micropayment rail for AI inference
KITE_RPC_URL = os.getenv("KITE_RPC_URL", "https://rpc.testnet.gokite.ai")
KITE_CHAIN_ID = 2368
KITE_PRIVATE_KEY = os.getenv("KITE_PRIVATE_KEY", "")
KITE_AGENT_ADDRESS = os.getenv("KITE_AGENT_ADDRESS", "")
# The designated AI inference provider address on Kite chain
KITE_INFERENCE_PROVIDER = os.getenv(
    "KITE_INFERENCE_PROVIDER", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
)
# Inference price: 0.001 KITE per call
KITE_INFERENCE_PRICE_ETH = float(os.getenv("KITE_INFERENCE_PRICE_ETH", "0.001"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# CoinMarketCap
COINMARKETCAP_API_KEY = os.getenv("COINMARKETCAP_API_KEY", "")

# Claude model
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# Per-chain contract addresses
# NOTE: aion_vault addresses are placeholders — update after deploying AionVault.
CHAIN_CONFIG = {
    "sepolia": {
        "rpc_url": SEPOLIA_RPC_URL,
        "aion_vault": os.getenv("SEPOLIA_AION_VAULT", "0x0000000000000000000000000000000000000000"),
        "ai_yield_engine": "0x4a8Ec2D9655600bc5d5D3460e8680251C839E61D",
        "autonomous_allocator": "0x7C9eF492Cc14A795d8BAa6937b4cF23F258Ce6f1",
        "price_oracle": "0xdBF02AeBf96D1C3E8B4E35f61C27A37cc6f601e4",
        "mock_usdc": "0x331cB2F787b2DC57855Bb30B51bE09aEF53e84C0",
        "ai_agent_registry": os.getenv("SEPOLIA_AI_AGENT_REGISTRY", ""),
        "ai_attestation_registry": os.getenv("SEPOLIA_AI_ATTESTATION_REGISTRY", ""),
    },
    "fuji": {
        "rpc_url": AVAX_FUJI_RPC_URL,
        "aion_vault": os.getenv("FUJI_AION_VAULT", "0x0000000000000000000000000000000000000000"),
        "ai_yield_engine": "0x104895cc071Fb53ba9d4851c0fe1B896dCEB558A",
        "autonomous_allocator": "0x5A6259254dA9d37081E2FAd716885ad8393a5408",
        "price_oracle": "0xbf8528f513111b8352cdc649A5C9031a83dB3e20",
        "mock_usdc": "0xa35C19170526eB8764a995fb5298eD1156B1b379",
        "ai_agent_registry": os.getenv("FUJI_AI_AGENT_REGISTRY", ""),
        "ai_attestation_registry": os.getenv("FUJI_AI_ATTESTATION_REGISTRY", ""),
    },
}


def get_chain_config(chain: str) -> dict:
    """Get config for a chain. Defaults to fuji if unknown."""
    return CHAIN_CONFIG.get(chain, CHAIN_CONFIG["fuji"])
