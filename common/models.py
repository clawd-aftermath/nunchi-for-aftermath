"""Shared Pydantic models for the trading system."""
from __future__ import annotations

from typing import Any, Collection, Dict, List, Optional, Set

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Instrument name registry
# ---------------------------------------------------------------------------

DEFAULT_SUFFIX = "-PERP"

# HIP-3 DEX definitions
HIP3_DEXS: Dict[str, Dict[str, Any]] = {
    "yex": {
        "coin_prefix": "yex:",
        "instrument_suffix": "-USDYP",
        "assets": frozenset({"VXX", "US3M", "BTCSWP"}),
    },
}

# Derived lookups
SPECIAL_ASSETS: Dict[str, str] = {}
HL_COIN_PREFIXES: Dict[str, str] = {}
DEX_BY_SUFFIX: Dict[str, str] = {}
for _dex_id, _dex in HIP3_DEXS.items():
    for _asset in _dex["assets"]:
        SPECIAL_ASSETS[_asset] = _dex["instrument_suffix"]
    HL_COIN_PREFIXES[_dex["instrument_suffix"]] = _dex["coin_prefix"]
    DEX_BY_SUFFIX[_dex["instrument_suffix"]] = _dex_id

INSTRUMENT_SUFFIXES = tuple({DEFAULT_SUFFIX} | set(SPECIAL_ASSETS.values()))


def asset_to_instrument(asset: str) -> str:
    return asset + SPECIAL_ASSETS.get(asset, DEFAULT_SUFFIX)


def instrument_to_coin(instrument: str) -> str:
    upper = instrument.upper()
    for suffix, prefix in HL_COIN_PREFIXES.items():
        if upper.endswith(suffix):
            return prefix + instrument[:-len(suffix)]
    if upper.endswith(DEFAULT_SUFFIX):
        return instrument[:-len(DEFAULT_SUFFIX)]
    return instrument


def instrument_to_asset(instrument: str) -> str:
    """Strip suffix to get bare asset name. VXX-USDYP -> VXX, ETH-PERP -> ETH."""
    upper = instrument.upper()
    for suffix in INSTRUMENT_SUFFIXES:
        if upper.endswith(suffix):
            return instrument[:-len(suffix)]
    return instrument


def coin_to_instrument(coin: str) -> str:
    for suffix, prefix in HL_COIN_PREFIXES.items():
        if coin.startswith(prefix):
            return coin[len(prefix):] + suffix
    return asset_to_instrument(coin)


def asset_to_coin(asset: str) -> str:
    suffix = SPECIAL_ASSETS.get(asset, DEFAULT_SUFFIX)
    prefix = HL_COIN_PREFIXES.get(suffix, "")
    return prefix + asset


def asset_matches_allowed(asset: str, allowed: Collection[str]) -> bool:
    """Check if a bare asset name matches any entry in an allowed instruments set."""
    if asset in allowed:
        return True
    return any(asset + suffix in allowed for suffix in INSTRUMENT_SUFFIXES)


def dex_for_instrument(instrument: str) -> Optional[str]:
    """Return HIP-3 dex ID for an instrument, or None for native perps."""
    for suffix, dex_id in DEX_BY_SUFFIX.items():
        if instrument.endswith(suffix):
            return dex_id
    return None


def get_hip3_dex_ids(instruments: Collection[str]) -> Set[str]:
    """Return set of HIP-3 dex IDs needed for a list of instruments."""
    return {d for inst in instruments for d in [dex_for_instrument(inst)] if d}


class MarketSnapshot(BaseModel):
    instrument: str = "ETH-PERP"
    mid_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread_bps: float = 0.0
    timestamp_ms: int = 0
    volume_24h: float = 0.0
    funding_rate: float = 0.0
    open_interest: float = 0.0


class VerifyResult(BaseModel):
    ok: bool
    checks: Dict[str, bool] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class StrategyDecision(BaseModel):
    action: str = "noop"  # "place_order" or "noop"
    instrument: str = "ETH-PERP"
    side: str = ""        # "buy" or "sell"
    size: float = 0.0
    limit_price: float = 0.0
    order_type: str = "Gtc"  # "Gtc" (rest on book), "Ioc" (cross spread), "Alo" (maker-only)
    meta: Dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    """Individual decision — matches KorAI MVP Listing 1 inner 'decision' object."""
    decision_id: str
    strategy_id: str = ""
    action: str = "limit_order"  # quote | limit_order | hedge
    instrument: str = "ETH"
    side: Optional[str] = None   # buy | sell | null
    size: float = 0.0
    limit_price: float = 0.0
    timestamp_ms: int = 0
