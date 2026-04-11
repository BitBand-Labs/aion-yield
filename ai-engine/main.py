"""AION Yield AI Strategy Engine - FastAPI Server (Multi-Chain).

Reads from AionVault (ERC4626) and produces AI-powered strategy
allocation and harvest recommendations using Anthropic Claude.

Endpoints:
  GET  /health                              - Health check
  GET  /vault?chain=fuji                    - Fetch vault state (TVL, strategies, tranches, PnL)
  GET  /vault/harvest-preview/{strategy}    - Preview harvest for a strategy
  GET  /price/{asset}?chain=fuji            - Fetch asset price from oracle
  GET  /external-apys                       - Fetch external protocol APYs
  GET  /market-context                      - Fetch macro market data from CoinMarketCap
  POST /analyze                             - AI-powered allocation & harvest recommendations
  POST /predict                             - AI-powered yield prediction

Supported chains: sepolia, fuji
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional

from config import HOST, PORT, ANTHROPIC_API_KEY, CHAIN_CONFIG, get_chain_config
from chain_reader import get_vault_data, get_harvest_preview, get_asset_price, fetch_external_apys, fetch_market_context
from ai_strategy import analyze_and_recommend, predict_yield
from kite_payment import pay_for_inference, get_session_spend, TOOL_PRICES
from attestation import post_attestation

app = FastAPI(
    title="AION Yield AI Strategy Engine",
    description="AI-powered ERC4626 vault yield optimization using Anthropic Claude (Avalanche Fuji + Ethereum Sepolia)",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "https://aionyield.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Models ──


class AnalyzeRequest(BaseModel):
    chain: str = Field(default="fuji", description="Chain: sepolia or fuji")


class PredictRequest(BaseModel):
    chain: str = Field(default="fuji", description="Chain: sepolia or fuji")
    timeframe_hours: int = Field(default=24, description="Prediction window in hours")


# ── Endpoints ──


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ai_configured": bool(ANTHROPIC_API_KEY),
        "service": "AION Yield AI Strategy Engine",
        "version": "3.0.0",
        "supported_chains": list(CHAIN_CONFIG.keys()),
    }


@app.get("/vault")
async def vault_state(
    chain: str = Query(default="fuji", description="Chain: sepolia or fuji"),
):
    """Fetch full AionVault state: TVL, tranches, strategies, unrealized PnL."""
    data = get_vault_data(chain=chain)
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/vault/harvest-preview/{strategy_address}")
async def harvest_preview(
    strategy_address: str,
    chain: str = Query(default="fuji", description="Chain: sepolia or fuji"),
):
    """Preview what would happen if a strategy is harvested now."""
    data = get_harvest_preview(strategy_address, chain=chain)
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/price/{asset_address}")
async def get_price(
    asset_address: str,
    chain: str = Query(default="fuji", description="Chain: sepolia or fuji"),
):
    """Fetch asset price from ChainlinkPriceOracle."""
    data = get_asset_price(asset_address, chain=chain)
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/external-apys")
async def external_apys():
    """Fetch APYs from external protocols (Aave V3, Compound, Benqi)."""
    return await fetch_external_apys()


@app.get("/market-context")
async def market_context():
    """Fetch macro market data from CoinMarketCap: asset prices, global metrics,
    stablecoin health, and market risk signal."""
    data = await fetch_market_context()
    if "error" in data:
        raise HTTPException(status_code=503, detail=data["error"])
    return data


@app.get("/kite-payments/session")
async def kite_session_spend():
    """Return KITE spending stats for this session."""
    return get_session_spend()


@app.get("/mcp-info")
async def mcp_info():
    """Describe the AION MCP server: available tools, KITE prices, and connection info."""
    return {
        "name": "aion-yield-mcp",
        "description": "AION Yield AI oracle — DeFi vault intelligence gated by Kite x402 micropayments",
        "version": "1.0.0",
        "kite_chain": {
            "chain_id": 2368,
            "rpc": "https://rpc.testnet.gokite.ai",
            "explorer": "https://testnet.kitescan.ai",
            "token": "KITE",
        },
        "transports": {
            "stdio": "python mcp_server.py",
            "sse": "python mcp_server.py --sse  (port 8001, /sse)",
        },
        "tools": [
            {
                "name": tool,
                "price_kite": price,
            }
            for tool, price in TOOL_PRICES.items()
            if not tool.startswith("ai_")
        ],
        "supported_chains": list(CHAIN_CONFIG.keys()),
    }


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    """AI-powered analysis: reads AionVault state, fetches external APYs and
    macro market context, then uses Claude to produce strategy allocation
    and harvest recommendations.

    Pays a KITE x402 micropayment before calling Claude, creating an
    on-chain attestation of the AI inference."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    vault_data = get_vault_data(chain=request.chain)
    if "error" in vault_data:
        raise HTTPException(status_code=500, detail=f"Vault read error: {vault_data['error']}")

    # x402: pay for inference on Kite chain before calling Claude
    payment_proof = pay_for_inference(action_type="analyze")

    # Get asset price for the underlying token
    cfg = get_chain_config(request.chain)
    price_data = get_asset_price(cfg["mock_usdc"], chain=request.chain)
    external = await fetch_external_apys()
    market = await fetch_market_context()

    result = await analyze_and_recommend(
        vault_data=vault_data,
        external_apys=external,
        asset_price=price_data if "error" not in price_data else None,
        market_context=market if "error" not in market else None,
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # Post on-chain attestation (fire-and-forget; never blocks the response)
    attestation_id = await post_attestation(
        action_type="analyze",
        output=result,
        kite_pay_tx=payment_proof.get("tx_hash") or "",
        chain=request.chain,
    )

    return {
        "chain": request.chain,
        "vault_state": vault_data,
        "market_context": market if "error" not in market else None,
        "ai_recommendation": result,
        "payment_proof": payment_proof,
        "attestation_id": attestation_id,
    }


@app.post("/predict")
async def predict(request: PredictRequest):
    """AI-powered yield prediction for the vault over a timeframe.

    Pays a KITE x402 micropayment before calling Claude, creating an
    on-chain attestation of the AI inference."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Anthropic API key not configured")

    vault_data = get_vault_data(chain=request.chain)
    if "error" in vault_data:
        raise HTTPException(status_code=500, detail=f"Vault read error: {vault_data['error']}")

    # x402: pay for inference on Kite chain before calling Claude
    payment_proof = pay_for_inference(action_type="predict")

    external = await fetch_external_apys()
    market = await fetch_market_context()

    result = await predict_yield(
        vault_data=vault_data,
        external_apys=external,
        timeframe_hours=request.timeframe_hours,
        market_context=market if "error" not in market else None,
    )

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    # Post on-chain attestation (fire-and-forget; never blocks the response)
    attestation_id = await post_attestation(
        action_type="predict",
        output=result,
        kite_pay_tx=payment_proof.get("tx_hash") or "",
        chain=request.chain,
    )

    return {
        "chain": request.chain,
        "vault_state": vault_data,
        "market_context": market if "error" not in market else None,
        "prediction": result,
        "payment_proof": payment_proof,
        "attestation_id": attestation_id,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
