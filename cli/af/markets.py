"""Market discovery, instrument normalisation and fixed-point scaling.

This module replaces Hyperliquid's dex-local asset-ID arithmetic
(``100000 + dex_index * 10000 + meta_index``) and its ``xyz:`` HIP-3 prefixes.
Those are gone, not re-pointed. Aftermath market ids are on-chain object ids
that the API validates strictly, so they are always *resolved from the API* and
never constructed from a ticker.

Two facts about ``/api/perpetuals/all-markets`` worth stating, because both
break naive code:

* it is **POST** and requires ``{"collateralCoinType": …}``;
* it returns ``{"markets": [...]}``, not a bare array.

Pre-relaunch there may be **zero markets**. That is expected, and is warned
about rather than treated as an outage.

Ordering note (v3.0.0): the API now guarantees deterministic ordering --
markets by symbol, positions by market id, bids and asks each by order id. That
ordering is preserved here rather than re-sorted.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from cli.af import api
from cli.af.ids import MarketId, coerce_bigint

log = logging.getLogger("af.markets")

_MARKET_TTL_S = 300.0


class NoSuchMarket(LookupError):
    """The requested instrument has no market on Aftermath."""


class NoMarketsAvailable(LookupError):
    """The venue currently lists no markets at all (expected pre-relaunch)."""


@dataclass(frozen=True)
class Market:
    """One perpetuals market, as resolved from the API.

    Only fields that exist in the v3.0.0 spec are surfaced. The removed
    parameters -- ``gasPriceTwapPeriodMs``, ``forceCancelFee``,
    ``gasPriceTakerFee`` and ``zScoreThreshold`` -- are deliberately absent;
    ``priority_taker_fee`` is their replacement.
    """

    market_id: MarketId
    symbol: str
    package_id: str
    collateral_coin_type: str
    index_price: float
    collateral_price: float
    estimated_funding_rate: float
    lot_size: int
    tick_size: int
    #: Converts COLLATERAL units to the internal fixed-point representation.
    #: This is NOT the price/size encoding -- see FIXED_POINT below.
    scaling_factor: float
    margin_ratio_initial: float
    margin_ratio_maintenance: float
    maker_fee: float
    taker_fee: float
    #: v3.0.0 replacement for the removed gasPriceTakerFee / zScoreThreshold.
    priority_taker_fee: Optional[float]
    min_order_usd_value: float
    max_pending_orders: int
    open_interest: float
    next_funding_timestamp_ms: int
    raw: Dict[str, Any]

    @property
    def instrument(self) -> str:
        """This repository's instrument name for the market, e.g. ``ETH-AF-PERP``."""
        return f"{self.symbol.upper()}-AF-PERP"


def _f(d: Dict[str, Any], key: str, default: float = 0.0) -> float:
    v = d.get(key)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(d: Dict[str, Any], key: str, default: int = 0) -> int:
    """Read an integer field that may arrive in BigInt ``"123n"`` form.

    Verified against the live API: ``lotSize``, ``tickSize``, ``maxPendingOrders``
    and the funding/TWAP period fields all come back as ``"100000n"`` strings.
    A plain ``int()`` raises on those, and swallowing the error would silently
    substitute a default -- which for ``tickSize`` means every order price is
    rounded to the wrong grid and rejected on-chain.
    """
    v = d.get(key)
    if v is None:
        return default
    try:
        return coerce_bigint(v)
    except TypeError:
        return default


def parse_market(raw: Dict[str, Any]) -> Market:
    """Build a :class:`Market` from one ``/all-markets`` element."""
    params = raw.get("marketParams") or {}
    state = raw.get("marketState") or {}
    priority = params.get("priorityTakerFee")

    return Market(
        market_id=MarketId(raw["objectId"]),
        symbol=str(params.get("baseAssetSymbol", "")).upper(),
        package_id=str(raw.get("packageId", "")),
        collateral_coin_type=str(raw.get("collateralCoinType", "")),
        index_price=_f(raw, "indexPrice"),
        collateral_price=_f(raw, "collateralPrice"),
        estimated_funding_rate=_f(raw, "estimatedFundingRate"),
        lot_size=_i(params, "lotSize", 1),
        tick_size=_i(params, "tickSize", 1),
        scaling_factor=_f(params, "scalingFactor", 1.0) or 1.0,
        margin_ratio_initial=_f(params, "marginRatioInitial"),
        margin_ratio_maintenance=_f(params, "marginRatioMaintenance"),
        maker_fee=_f(params, "makerFee"),
        taker_fee=_f(params, "takerFee"),
        priority_taker_fee=float(priority) if priority is not None else None,
        min_order_usd_value=_f(params, "minOrderUsdValue"),
        max_pending_orders=_i(params, "maxPendingOrders", 0),
        open_interest=_f(state, "openInterest"),
        next_funding_timestamp_ms=_i(raw, "nextFundingTimestampMs"),
        raw=raw,
    )


class MarketRegistry:
    """Caches the market universe with explicit invalidation.

    Cached because every order needs the market's lot/tick sizes, and refetching
    per order would triple the request count on a market-making loop. TTL is
    short and :meth:`invalidate` exists for anything that changes the universe.
    """

    def __init__(self, collateral_coin_type: str, ttl_s: float = _MARKET_TTL_S):
        self.collateral_coin_type = collateral_coin_type
        self.ttl_s = ttl_s
        self._by_id: Dict[str, Market] = {}
        self._by_symbol: Dict[str, Market] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._fetched_at = 0.0

    def _fresh(self) -> bool:
        return bool(self._by_id) and (time.time() - self._fetched_at) < self.ttl_s

    def refresh(self, force: bool = False) -> List[Market]:
        with self._lock:
            if self._fresh() and not force:
                return list(self._by_id.values())

            res = api.post(
                "/api/perpetuals/all-markets",
                {"collateralCoinType": self.collateral_coin_type},
            )
            # Response is {"markets": [...]}, NOT a bare array.
            if not isinstance(res, dict) or "markets" not in res:
                raise api.AfApiError(
                    f"/api/perpetuals/all-markets returned an unexpected body: {type(res).__name__}"
                )
            raw_markets = res.get("markets") or []

            by_id: Dict[str, Market] = {}
            by_symbol: Dict[str, Market] = {}
            for raw in raw_markets:
                try:
                    m = parse_market(raw)
                except (KeyError, TypeError) as exc:
                    log.warning("skipping unparseable market entry: %s", exc)
                    continue
                by_id[str(m.market_id)] = m
                for alias in symbol_aliases(m.symbol):
                    by_symbol.setdefault(alias, m)

            self._by_id, self._by_symbol = by_id, by_symbol
            self._fetched_at = time.time()

            if not by_id:
                # Expected pre-relaunch. Warn, never fail.
                log.warning(
                    "Aftermath lists no markets for collateral %s. This is expected "
                    "before the relaunch; trading paths will stay idle.",
                    self.collateral_coin_type,
                )
            return list(by_id.values())

    def all(self) -> List[Market]:
        """Every market, in the API's own (symbol-sorted) order."""
        return self.refresh()

    def get(self, instrument: str) -> Market:
        """Resolve an instrument name or market id to a :class:`Market`."""
        markets = self.refresh()
        if not markets:
            raise NoMarketsAvailable(
                "Aftermath lists no markets yet — nothing to trade. "
                "This is expected before the relaunch."
            )

        key = str(instrument).strip()
        if key in self._by_id:
            return self._by_id[key]

        symbol = base_asset(key)
        if symbol in self._by_symbol:
            return self._by_symbol[symbol]

        raise NoSuchMarket(
            f"no Aftermath market for {instrument!r} (base asset {symbol!r}). "
            f"Available: {', '.join(sorted(self._by_symbol)) or '<none>'}"
        )

    def market_id(self, instrument: str) -> MarketId:
        return self.get(instrument).market_id


# ── Instrument normalisation ─────────────────────────────────────
# Strategies and configs use several naming conventions. All of them are mapped
# onto one canonical form here so a strategy written for "ETH-PERP" runs
# unchanged. Hyperliquid's `xyz:` HIP-3 prefix is stripped rather than
# translated -- there is no such namespace on Aftermath.

_HL_DEX_PREFIX = re.compile(r"^[a-z0-9]+:", re.IGNORECASE)

#: Quote-currency suffixes that appear glued onto a base symbol.
#: The live BTC market reports `baseAssetSymbol == "BTCUSD"`, so a strategy
#: asking for plain "BTC" must still resolve. Registered as aliases rather than
#: stripped from the canonical symbol, so the API's own name stays authoritative.
_GLUED_QUOTES = ("USDC", "USDT", "USD")


def symbol_aliases(symbol: str) -> List[str]:
    """Every name a market should answer to.

    ``"BTCUSD"`` -> ``["BTCUSD", "BTC"]``; ``"ETH"`` -> ``["ETH"]``.
    """
    s = (symbol or "").strip().upper()
    if not s:
        return []
    out = [s]
    for q in _GLUED_QUOTES:
        if s.endswith(q) and len(s) > len(q):
            stripped = s[: -len(q)].rstrip("-_/")
            if stripped and stripped not in out:
                out.append(stripped)
            break
    return out


def base_asset(instrument: str) -> str:
    """Extract the base asset symbol from any supported instrument spelling.

    ``ETH-AF-PERP`` -> ``ETH``; ``xyz:BTC-PERP`` -> ``BTC``; ``SUI`` -> ``SUI``.
    """
    s = str(instrument).strip().upper()
    s = _HL_DEX_PREFIX.sub("", s)  # drop any venue/dex namespace prefix
    s = s.replace("/USDC:USDC", "").replace("/USDC", "").replace("/USD", "")
    for suffix in ("-AF-PERP", "-PERP", "-USDC", "-USDT", "-USD"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip("-_ ").upper()


def normalise_instrument(instrument: str) -> str:
    """Canonical instrument name for this repository: ``<BASE>-AF-PERP``."""
    base = base_asset(instrument)
    if not base:
        raise ValueError(f"cannot derive a base asset from {instrument!r}")
    return f"{base}-AF-PERP"


# ── Fixed-point scaling ──────────────────────────────────────────
# Prices and sizes cross the wire as scaled integers in BigInt "…n" strings.
#
# The encoding is 9 decimals. This was verified empirically against the live
# BTCUSD market on 2026-07-28: for all 12 orderbook levels, `price * 1e9` was an
# exact multiple of `tickSize` (100000) and `size * 1e9` an exact multiple of
# `lotSize` (1). Twelve out of twelve on both is not a coincidence.
#
# `marketParams.scalingFactor` is NOT this number. It was 1e-06 on that market,
# and the spec describes it as converting *collateral* units -- using it for
# price would scale a $63,828 order down to 0.
#
# Rounding must respect tick and lot size or the order is rejected on-chain,
# which costs a round-trip instead of failing cleanly here.

#: Fixed-point denominator for prices and sizes on the wire (9 decimals).
FIXED_POINT = 10**9


def scale_price(price: float, market: Market) -> int:
    """Convert a float price to its wire integer, snapped DOWN to tick size."""
    raw = int(round(float(price) * FIXED_POINT))
    tick = max(1, market.tick_size)
    return max(0, (raw // tick) * tick)


def scale_size(size: float, market: Market) -> int:
    """Convert a float size to its wire integer, snapped DOWN to lot size."""
    raw = int(round(float(size) * FIXED_POINT))
    lot = max(1, market.lot_size)
    return max(0, (raw // lot) * lot)


def unscale(value: int) -> float:
    """Convert a wire integer back to a float."""
    return float(value) / FIXED_POINT
