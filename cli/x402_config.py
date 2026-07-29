"""x402 agentic payment configuration.

The x402 protocol enables agents to pay for services (LLM calls, data, compute)
using USDC micropayments signed by an EVM wallet (Base) or Solana wallet.
No API keys needed — the wallet signature IS the auth.

Used by ClawRouter (BlockRunAI/ClawRouter) for pay-per-call LLM routing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class X402Config:
    """Configuration for x402 micropayment-based LLM access via ClawRouter."""

    wallet_key: str = ""           # EVM private key for signing x402 payments
    payment_chain: str = "base"    # "base" (EVM) or "solana"
    proxy_port: int = 8402         # ClawRouter local proxy port

    @property
    def proxy_url(self) -> str:
        return f"http://localhost:{self.proxy_port}"

    @property
    def enabled(self) -> bool:
        return bool(self.wallet_key)

    @classmethod
    def from_env(cls) -> "X402Config":
        return cls(
            wallet_key=os.environ.get("BLOCKRUN_WALLET_KEY", ""),
            payment_chain=os.environ.get("BLOCKRUN_PAYMENT_CHAIN", "base"),
            proxy_port=int(os.environ.get("BLOCKRUN_PROXY_PORT", "8402")),
        )
