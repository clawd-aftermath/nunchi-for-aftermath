"""Offline twin of :class:`cli.af.proxy.AftermathProxy`.

Every strategy in the repository must be runnable with zero network and zero
keys. This is what makes that true, and it is also what makes the test suite
meaningful without a live venue: strategies exercise the same call shape they
use in production.

Interface parity with the real adapter is enforced by
``tests/test_af_v2_adapter.py``, which compares the two classes method by
method and signature by signature. Parity by test rather than by discipline is
deliberate -- a mock that drifts from its subject is worse than no mock,
because it makes green tests meaningless.
"""
from __future__ import annotations

import logging
import math
import random
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from common.models import MarketSnapshot

from cli.af.config import AfConfig
from cli.af.gas import GasConfig
from cli.af.markets import normalise_instrument, base_asset
from cli.af.proxy import AfFill
from cli.af.safety import CircuitBreaker, KillSwitch, MarginHealth, SAFE

log = logging.getLogger("af.mock")

#: Plausible starting prices so a strategy's own sanity checks pass.
_BASE_PRICES = {
    "ETH": 2500.0,
    "BTC": 65000.0,
    "SUI": 3.20,
    "XAG": 31.50,
    "SOL": 150.0,
}


class AftermathMockProxy:
    """Simulated Aftermath venue. No network, no keys, no signing."""

    def __init__(
        self,
        base_price: float = 0.0,
        spread_bps: float = 2.0,
        config: Optional[AfConfig] = None,
        *,
        signer: Any = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        seed: Optional[int] = None,
    ):
        self.config = config or AfConfig()
        self._signer = signer
        self.breaker = circuit_breaker or CircuitBreaker()
        self.spread_bps = spread_bps
        self._base_price_override = base_price
        self._rng = random.Random(seed if seed is not None else 20260728)
        self._t0 = time.time()

        self._orders: Dict[str, Dict[str, Any]] = {}
        self._next_oid = 1000
        self._positions: Dict[str, float] = {}
        self._allocated: Dict[str, float] = {}
        self._collateral = 10_000.0

        self.kill_switch = KillSwitch(
            self.config.heartbeat_timeout_s, cancel_all=self.cancel_all_verified
        )

    # ── VenueAdapter surface ─────────────────────────────────────

    def connect(self, private_key: str = "", testnet: bool = False) -> None:
        """No-op. The mock never accepts a secret, exactly like the real adapter."""
        if private_key:
            log.warning("mock connect() ignoring a supplied private key")

    def capabilities(self):  # noqa: ANN201
        from common.venue_adapter import VenueCapabilities

        return VenueCapabilities(
            supports_alo=True,
            supports_trigger_orders=True,
            supports_builder_fee=True,
            supports_cross_margin=False,
        )

    @property
    def gas(self) -> GasConfig:
        return GasConfig(
            mode=self.config.gas_mode,
            budget_mist=self.config.gas_budget_mist,
            sponsor="0x" + "ab" * 32 if self.config.gas_mode == "sponsored" else None,
            gas_coin_type=self.config.gas_coin_type,
        )

    # ── Pricing ──────────────────────────────────────────────────

    def _price(self, instrument: str) -> float:
        """A gently oscillating price, deterministic for a given seed."""
        if self._base_price_override > 0:
            base = self._base_price_override
        else:
            base = _BASE_PRICES.get(base_asset(instrument), 100.0)
        drift = math.sin((time.time() - self._t0) / 30.0) * 0.002
        noise = (self._rng.random() - 0.5) * 0.0004
        return base * (1.0 + drift + noise)

    def get_snapshot(self, instrument: str = "ETH-AF-PERP") -> MarketSnapshot:
        inst = normalise_instrument(instrument or "ETH-AF-PERP")
        mid = self._price(inst)
        half = mid * (self.spread_bps / 2.0 / 10_000.0)
        return MarketSnapshot(
            instrument=inst,
            mid_price=mid,
            bid=mid - half,
            ask=mid + half,
            spread_bps=self.spread_bps,
            timestamp_ms=int(time.time() * 1000),
            volume_24h=1_000_000.0,
            funding_rate=0.0001,
            open_interest=5_000_000.0,
        )

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        inst = normalise_instrument(coin)
        step_ms = 60_000
        n = max(1, min(500, int(lookback_ms) // step_ms))
        now = int(time.time() * 1000)
        out: List[Dict] = []
        price = self._price(inst)
        for i in range(n, 0, -1):
            ts = now - i * step_ms
            wobble = (self._rng.random() - 0.5) * price * 0.001
            o = price + wobble
            c = price - wobble
            out.append(
                {
                    "t": ts,
                    "T": ts + step_ms,
                    "o": f"{o:.6f}",
                    "h": f"{max(o, c) * 1.0005:.6f}",
                    "l": f"{min(o, c) * 0.9995:.6f}",
                    "c": f"{c:.6f}",
                    "v": "1000",
                    "s": base_asset(coin),
                }
            )
        return out

    def get_all_markets(self) -> Any:
        return [
            {
                "name": sym,
                "instrument": f"{sym}-AF-PERP",
                "marketId": "0x" + f"{i:064x}",
                "maxLeverage": 10.0,
                "marginRatioInitial": 0.1,
                "marginRatioMaintenance": 0.05,
                "makerFee": 0.0002,
                "takerFee": 0.0006,
                "priorityTakerFee": None,
                "minOrderUsdValue": 10.0,
                "openInterest": 5_000_000.0,
                "fundingRate": 0.0001,
            }
            for i, sym in enumerate(sorted(_BASE_PRICES), start=1)
        ]

    def get_all_mids(self) -> Dict[str, str]:
        return {sym: str(self._price(sym)) for sym in sorted(_BASE_PRICES)}

    # ── Account ──────────────────────────────────────────────────

    def get_account_state(self) -> Dict:
        positions = []
        for inst, qty in self._positions.items():
            if qty == 0:
                continue
            px = self._price(inst)
            positions.append(
                {
                    "position": {
                        "coin": base_asset(inst),
                        "szi": str(qty),
                        "entryPx": str(px),
                        "positionValue": str(abs(qty) * px),
                        "unrealizedPnl": "0.0",
                        "marginUsed": str(abs(qty) * px / max(1.0, self.config.leverage)),
                        "liquidationPx": str(px * 0.8),
                        "leverage": {"type": "isolated", "value": self.config.leverage},
                        "marginRatio": 0.25,
                        "marketId": "0x" + "00" * 32,
                    }
                }
            )
        return {
            "marginSummary": {
                "accountValue": str(self._collateral),
                "totalMarginUsed": str(sum(float(p["position"]["marginUsed"]) for p in positions)),
            },
            "assetPositions": positions,
            "withdrawable": str(self._collateral),
            "accountId": "1n",
        }

    def has_position(self, instrument: str) -> bool:
        return self._positions.get(normalise_instrument(instrument), 0.0) != 0.0

    def margin_health(self, instrument: str) -> Optional[MarginHealth]:
        if not self.has_position(instrument):
            return None
        return MarginHealth(zone=SAFE, margin_ratio=0.25, maintenance_ratio=0.05, buffer_multiple=5.0)

    def resolve_account(self, refresh: bool = False):  # noqa: ANN201
        from cli.af.ids import NativeAccountId
        from cli.af.proxy import AccountRef

        return AccountRef(
            account_id=NativeAccountId(1),
            cap_id=None,
            collateral_coin_type=self.config.collateral_coin_type,
            collateral=int(self._collateral),
        )

    @property
    def account(self):  # noqa: ANN201
        return self.resolve_account()

    # ── Collateral ───────────────────────────────────────────────

    def allocate_collateral(self, instrument: str, amount: float) -> Optional[str]:
        self._allocated[normalise_instrument(instrument)] = amount
        return f"mock-alloc-{int(time.time() * 1000)}"

    # ── Orders ───────────────────────────────────────────────────

    def _new_oid(self) -> str:
        self._next_oid += 1
        return str(self._next_oid)

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[Dict] = None,
    ) -> Optional[AfFill]:
        if size <= 0 or price <= 0:
            return None
        inst = normalise_instrument(instrument)
        oid = self._new_oid()
        snap = self.get_snapshot(inst)

        # IOC crosses the book and fills; resting types stay on the book.
        crosses = str(tif).lower() in ("ioc", "market", "taker", "fok") and (
            (side.lower() == "buy" and price >= snap.ask) or (side.lower() == "sell" and price <= snap.bid)
        )
        if crosses:
            delta = size if side.lower() == "buy" else -size
            self._positions[inst] = self._positions.get(inst, 0.0) + delta
            return AfFill(
                oid=oid,
                instrument=inst,
                side=side.lower(),
                price=Decimal(str(price)),
                quantity=Decimal(str(size)),
                timestamp_ms=int(time.time() * 1000),
            )

        self._orders[oid] = {
            "oid": oid,
            "coin": base_asset(inst),
            "instrument": inst,
            "side": "B" if side.lower() == "buy" else "A",
            "sz": str(size),
            "origSz": str(size),
            "limitPx": str(price),
            "clientOrderId": None,
            "marketId": "0x" + "00" * 32,
        }
        return AfFill(
            oid=oid,
            instrument=inst,
            side=side.lower(),
            price=Decimal(str(price)),
            quantity=Decimal(str(size)),
            timestamp_ms=int(time.time() * 1000),
        )

    def cancel_and_place_orders(
        self,
        instrument: str,
        cancel_oids: Sequence[str],
        new_orders: Sequence[Dict],
    ) -> List[Optional[AfFill]]:
        """Atomic in the same sense as the real adapter: all-or-nothing."""
        for oid in cancel_oids:
            self._orders.pop(str(oid), None)
        out: List[Optional[AfFill]] = []
        for o in new_orders:
            out.append(
                self.place_order(
                    instrument=instrument,
                    side=str(o.get("side", "buy")),
                    size=float(o.get("size", 0)),
                    price=float(o.get("price", 0)),
                    tif=str(o.get("tif", "Alo")),
                )
            )
        return out

    def place_scale_order(
        self,
        instrument: str,
        side: str,
        total_size: float,
        start_price: float,
        end_price: float,
        num_orders: int,
        tif: str = "Alo",
        size_skew: Optional[float] = None,
    ) -> Optional[str]:
        n = max(1, int(num_orders))
        per = total_size / n
        step = (end_price - start_price) / max(1, n - 1) if n > 1 else 0.0
        for i in range(n):
            self.place_order(instrument, side, per, start_price + step * i, tif)
        return f"mock-scale-{int(time.time() * 1000)}"

    def cancel_order(self, instrument: str, oid: str) -> bool:
        return self._orders.pop(str(oid), None) is not None

    def cancel_orders(self, instrument: str, oids: Sequence[str]) -> bool:
        for oid in oids:
            self._orders.pop(str(oid), None)
        return True

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        if not instrument:
            return list(self._orders.values())
        inst = normalise_instrument(instrument)
        return [o for o in self._orders.values() if o["instrument"] == inst]

    def cancel_all_verified(self) -> None:
        self._orders.clear()
        if self._orders:  # pragma: no cover - defensive parity with the real path
            raise RuntimeError("mock cancel-all failed verification")

    # ── Leverage / triggers ──────────────────────────────────────

    def set_leverage(self, leverage: int, coin: str = "ETH", is_cross: bool = True) -> None:
        self.config.leverage = float(leverage)

    def place_trigger_order(
        self,
        instrument: str,
        side: str,
        size: float,
        trigger_price: float,
        builder: Optional[Dict] = None,
        is_take_profit: bool = False,
    ) -> Optional[str]:
        return f"mock-trigger-{self._new_oid()}"

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        return True

    def max_order_size(self, instrument: str, side: str, price: Optional[float] = None) -> Optional[float]:
        px = price or self._price(instrument)
        return (self._collateral * self.config.leverage) / px if px > 0 else None
