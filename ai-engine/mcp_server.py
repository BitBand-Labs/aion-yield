"""AION Yield MCP Server — Kite x402 gated AI oracle for DeFi agents.

Exposes AION's vault intelligence as Model Context Protocol (MCP) tools.
Every tool call triggers a Kite x402 micropayment before data is returned,
creating on-chain attestation that an autonomous agent paid for the data.

Tools (with tiered KITE pricing):
  get_vault_state              → 0.001 KITE  Live TVL, strategies, tranches
  get_market_context           → 0.001 KITE  Macro market data (CMC)
  get_agent_reputation         → 0.001 KITE  AI agent registry lookup
  get_yield_prediction         → 0.002 KITE  Claude AI yield forecast
  get_allocation_recommendation→ 0.005 KITE  Claude AI strategy allocation

Usage:
  python mcp_server.py               # stdio transport (Claude Desktop / agents)
  python mcp_server.py --sse         # SSE transport (HTTP clients)

MCP clients connect and call tools; AION pays KITE per call as proof of work.
"""

import asyncio
import json
import sys
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from config import get_chain_config, CHAIN_CONFIG
from chain_reader import get_vault_data, fetch_external_apys, fetch_market_context
from ai_strategy import analyze_and_recommend, predict_yield
from kite_payment import pay_for_inference, get_tool_price, TOOL_PRICES

# ── Server Init ────────────────────────────────────────────────────────────────

server = Server("aion-yield-mcp")

# ── Tool Definitions ───────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """Advertise all available AION oracle tools to connecting MCP clients."""
    return [
        types.Tool(
            name="get_vault_state",
            description=(
                f"[{get_tool_price('get_vault_state')} KITE] "
                "Fetch live AION vault state: TVL, idle capital, total debt, "
                "senior/junior tranche sizes, active strategies, and unrealized PnL. "
                "Supports chains: sepolia, fuji."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chain": {
                        "type": "string",
                        "enum": list(CHAIN_CONFIG.keys()),
                        "description": "Target chain (sepolia or fuji)",
                        "default": "fuji",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="get_market_context",
            description=(
                f"[{get_tool_price('get_market_context')} KITE] "
                "Fetch macro DeFi market data: asset prices, global market cap, "
                "stablecoin health, volatility signals, and DeFi risk score. "
                "Sourced from CoinMarketCap with real-time data."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="get_agent_reputation",
            description=(
                f"[{get_tool_price('get_agent_reputation')} KITE] "
                "Look up an AI agent's on-chain reputation score, stake, task history, "
                "and accuracy from AION's AIAgentRegistry contract. "
                "Supports chains: sepolia, fuji."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_address": {
                        "type": "string",
                        "description": "Ethereum address of the AI agent to query",
                    },
                    "chain": {
                        "type": "string",
                        "enum": list(CHAIN_CONFIG.keys()),
                        "description": "Target chain (sepolia or fuji)",
                        "default": "fuji",
                    },
                },
                "required": ["agent_address"],
            },
        ),
        types.Tool(
            name="get_yield_prediction",
            description=(
                f"[{get_tool_price('get_yield_prediction')} KITE] "
                "Get Claude AI-powered yield prediction for AION vault over a given "
                "timeframe. Returns predicted senior/junior/blended APY, trend "
                "(increasing|stable|decreasing), confidence score, and key factors. "
                "Supports chains: sepolia, fuji."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chain": {
                        "type": "string",
                        "enum": list(CHAIN_CONFIG.keys()),
                        "description": "Target chain (sepolia or fuji)",
                        "default": "fuji",
                    },
                    "timeframe_hours": {
                        "type": "integer",
                        "description": "Prediction window in hours (1–168)",
                        "default": 24,
                        "minimum": 1,
                        "maximum": 168,
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="get_allocation_recommendation",
            description=(
                f"[{get_tool_price('get_allocation_recommendation')} KITE] "
                "Get Claude AI-powered strategy allocation recommendation for AION vault. "
                "Returns recommended debt levels per strategy, harvest targets, "
                "projected APY, and overall reasoning. Premium tool — costs more KITE "
                "due to deeper analysis. Supports chains: sepolia, fuji."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chain": {
                        "type": "string",
                        "enum": list(CHAIN_CONFIG.keys()),
                        "description": "Target chain (sepolia or fuji)",
                        "default": "fuji",
                    }
                },
                "required": [],
            },
        ),
    ]


# ── Tool Execution ─────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """Execute a tool: pay KITE first, then fetch data, return both."""

    # ── 1. Kite x402 payment (always first) ───────────────────────────────────
    payment = pay_for_inference(action_type=name)

    payment_banner = _format_payment_banner(payment)

    # ── 2. Dispatch to tool logic ──────────────────────────────────────────────
    try:
        if name == "get_vault_state":
            result = await _tool_vault_state(arguments)

        elif name == "get_market_context":
            result = await _tool_market_context(arguments)

        elif name == "get_agent_reputation":
            result = await _tool_agent_reputation(arguments)

        elif name == "get_yield_prediction":
            result = await _tool_yield_prediction(arguments)

        elif name == "get_allocation_recommendation":
            result = await _tool_allocation_recommendation(arguments)

        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        result = {"error": str(exc)}

    # ── 3. Combine payment proof + data in response ────────────────────────────
    response = {
        "payment_proof": payment,
        "data": result,
    }

    return [
        types.TextContent(
            type="text",
            text=payment_banner + "\n\n" + json.dumps(response, indent=2),
        )
    ]


# ── Tool Implementations ───────────────────────────────────────────────────────

async def _tool_vault_state(args: dict) -> dict:
    chain = args.get("chain", "fuji")
    data = get_vault_data(chain=chain)
    return data


async def _tool_market_context(args: dict) -> dict:
    data = await fetch_market_context()
    return data


async def _tool_agent_reputation(args: dict) -> dict:
    from web3 import Web3
    chain = args.get("chain", "fuji")
    agent_address = args.get("agent_address", "")

    if not agent_address:
        return {"error": "agent_address is required"}

    cfg = get_chain_config(chain)
    # AIAgentRegistry ABI — minimal view functions
    REGISTRY_ABI = json.loads("""[
        {
            "inputs": [{"name": "agent", "type": "address"}],
            "name": "reputationScores",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [{"name": "agent", "type": "address"}],
            "name": "isAgentActive",
            "outputs": [{"name": "", "type": "bool"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [{"name": "agent", "type": "address"}],
            "name": "successfulPredictions",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [{"name": "agent", "type": "address"}],
            "name": "failedPredictions",
            "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "inputs": [{"name": "agent", "type": "address"}],
            "name": "getAgentAccuracy",
            "outputs": [{"name": "successRate", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]""")

    # AIAgentRegistry address is not in chain config yet — use ai_yield_engine as proxy chain
    # In production this would be cfg["ai_agent_registry"]
    registry_addr = cfg.get("ai_agent_registry")
    if not registry_addr:
        return {
            "agent_address": agent_address,
            "chain": chain,
            "note": "AIAgentRegistry address not configured for this chain — add 'ai_agent_registry' to CHAIN_CONFIG",
        }

    try:
        w3 = Web3(Web3.HTTPProvider(cfg["rpc_url"]))
        registry = w3.eth.contract(
            address=Web3.to_checksum_address(registry_addr),
            abi=REGISTRY_ABI,
        )
        addr = Web3.to_checksum_address(agent_address)
        reputation = registry.functions.reputationScores(addr).call()
        is_active = registry.functions.isAgentActive(addr).call()
        successful = registry.functions.successfulPredictions(addr).call()
        failed = registry.functions.failedPredictions(addr).call()
        accuracy_bps = registry.functions.getAgentAccuracy(addr).call()

        return {
            "agent_address": agent_address,
            "chain": chain,
            "is_active": is_active,
            "reputation_score": reputation,
            "successful_predictions": successful,
            "failed_predictions": failed,
            "accuracy_pct": round(accuracy_bps / 100, 2),
        }
    except Exception as exc:
        return {"error": str(exc), "agent_address": agent_address, "chain": chain}


async def _tool_yield_prediction(args: dict) -> dict:
    chain = args.get("chain", "fuji")
    timeframe_hours = int(args.get("timeframe_hours", 24))

    vault_data = get_vault_data(chain=chain)
    if "error" in vault_data:
        return vault_data

    external = await fetch_external_apys()
    market = await fetch_market_context()

    result = await predict_yield(
        vault_data=vault_data,
        external_apys=external,
        timeframe_hours=timeframe_hours,
        market_context=market if "error" not in market else None,
    )
    return result


async def _tool_allocation_recommendation(args: dict) -> dict:
    from chain_reader import get_asset_price
    chain = args.get("chain", "fuji")

    vault_data = get_vault_data(chain=chain)
    if "error" in vault_data:
        return vault_data

    cfg = get_chain_config(chain)
    price_data = get_asset_price(cfg["mock_usdc"], chain=chain)
    external = await fetch_external_apys()
    market = await fetch_market_context()

    result = await analyze_and_recommend(
        vault_data=vault_data,
        external_apys=external,
        asset_price=price_data if "error" not in price_data else None,
        market_context=market if "error" not in market else None,
    )
    return result


# ── Payment Banner ─────────────────────────────────────────────────────────────

def _format_payment_banner(payment: dict) -> str:
    """Format a human-readable payment status header for tool responses."""
    if payment["paid"]:
        return (
            f"✓ KITE Payment: {payment['amount_kite']} KITE paid\n"
            f"  TX: {payment['tx_hash']}\n"
            f"  Explorer: {payment['explorer_url']}"
        )
    else:
        return (
            f"⚠ KITE Payment skipped: {payment['error']}\n"
            f"  (Tool data returned; fund wallet for on-chain attestation)"
        )


# ── Entry Point ────────────────────────────────────────────────────────────────

async def main():
    use_sse = "--sse" in sys.argv

    if use_sse:
        # SSE transport — HTTP clients (e.g. web frontends, REST consumers)
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route, Mount
        import uvicorn

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await server.run(
                    streams[0],
                    streams[1],
                    server.create_initialization_options(),
                )

        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages/", app=sse.handle_post_message),
            ]
        )

        port = 8001
        print(f"AION MCP server (SSE) listening on http://0.0.0.0:{port}/sse")
        print(f"Tool prices: {TOOL_PRICES}")
        uvicorn.run(starlette_app, host="0.0.0.0", port=port)

    else:
        # stdio transport — default for Claude Desktop and agent SDKs
        print("AION MCP server (stdio) starting…", file=sys.stderr)
        print(f"Tool prices: {TOOL_PRICES}", file=sys.stderr)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


if __name__ == "__main__":
    asyncio.run(main())
