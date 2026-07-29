"""The Aftermath V2 adapter.

This is the one module the rest of the repository trades through. It exposes
the call shape ``cli/engine.py`` and ``cli/order_manager.py`` already use, and
implements it entirely against Aftermath V2 -- so every strategy in the tree
runs on Aftermath without being individually rewritten. That is the whole
trick, and it is why no strategy file contains a venue call.

What lives here on purpose
--------------------------
Isolated-margin collateral allocation, the preview gate, transaction
inspection, ID typing, the BigInt wire format, state refresh after every
mutation, serialized deposits, the circuit breaker and the kill switch. Putting
them in the adapter is what makes them apply to all strategies at once and
impossible for one of them to bypass.

What is NOT here
----------------
Any Hyperliquid concept. ``marginPct``-plus-leverage sizing, ``xyz:`` HIP-3
prefixes, dex-local asset-id arithmetic, ``sendAsset``/``destinationDex`` and
the HL instrument universe are removed rather than re-pointed. The Aftermath
model is different in kind: isolated margin, explicit collateral allocation,
and API-validated market ids.

SAFETY POSTURE
--------------
No signer is wired up in this repository and nothing here signs, submits or
broadcasts a transaction. Mutating paths build, preview and inspect, then stop
at :meth:`AftermathProxy._submit`, which raises unless the process is armed
(``AF_ARMED=true``) *and* a signer has been injected. Read paths are fully
live.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence

from common.models import MarketSnapshot

from cli.af import api, gas as gasmod
from cli.af.config import AfConfig, load_config
from cli.af.ids import (
    AccountCapId,
    NativeAccountId,
    order_type_from_tif,
    side_to_int,
    to_native_bigint,
    coerce_bigint,
    TRIGGER_PRICE_MARK,
)
from cli.af.markets import (
    Market,
    MarketRegistry,
    NoMarketsAvailable,
    NoSuchMarket,
    base_asset,
    normalise_instrument,
    scale_price,
    scale_size,
)
from cli.af.safety import (
    BotState,
    CircuitBreaker,
    KillSwitch,
    assess_margin_health,
)
from cli.af.tx import (
    InspectedTx,
    NotArmedError,
    TxExpectation,
    TxInspectionError,
    build_gated,
    reconcile,
)

log = logging.getLogger("af.proxy")

#: Sui object operations race on object versions. Every mutation -- and
#: especially every deposit -- is serialized through this lock, because
#: parallel deposits fail with version/equivocation errors that look like
#: random API flakiness.
_write_lock = threading.RLock()


@dataclass
class AfFill:
    """A fill, in the shape the engine's position tracker expects.

    Structurally compatible with the venue-agnostic ``Fill`` and with the
    legacy ``HLFill`` the engine was written against, but defined here so the
    Aftermath execution path imports nothing from a Hyperliquid module.
    """

    oid: str
    instrument: str
    side: str
    price: Decimal
    quantity: Decimal
    timestamp_ms: int
    fee: Decimal = Decimal("0")


@dataclass
class AccountRef:
    """A resolved perpetuals account.

    Carries both identities deliberately: ``account_id`` is the numeric native
    id (wire form ``"123n"``) and ``cap_id`` is the account-capability OBJECT
    id. They are not interchangeable, and the type system here says so.
    """

    account_id: NativeAccountId
    cap_id: Optional[AccountCapId]
    collateral_coin_type: str
    collateral: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


class AftermathProxy:
    """Aftermath V2 venue adapter.

    Interface parity with :class:`cli.af.mock.AftermathMockProxy` is enforced
    by ``tests/test_af_v2_adapter.py`` rather than by discipline.
    """

    def __init__(
        self,
        config: Optional[AfConfig] = None,
        *,
        signer: Optional[Callable[[str], str]] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.config = config or load_config()
        self._signer = signer  # None in this repository. Nothing signs.
        self.breaker = circuit_breaker or CircuitBreaker()

        self.markets = MarketRegistry(self.config.collateral_coin_type)
        self._account: Optional[AccountRef] = None
        self._account_state_cache: Optional[Dict[str, Any]] = None
        self._positions_cache: Optional[Dict[str, Any]] = None
        self._allocated: Dict[str, float] = {}

        self.kill_switch = KillSwitch(
            self.config.heartbeat_timeout_s,
            cancel_all=self.cancel_all_verified,
        )

        self._gas = gasmod.GasConfig(
            mode=self.config.gas_mode,
            budget_mist=self.config.gas_budget_mist,
            sponsor=None,
            gas_coin_type=self.config.gas_coin_type,
        )

    # ── VenueAdapter surface ─────────────────────────────────────

    def connect(self, private_key: str = "", testnet: bool = False) -> None:
        """Resolve the account and market universe.

        The ``private_key`` parameter exists for signature compatibility with
        the venue-adapter ABC and is deliberately IGNORED: this adapter never
        accepts, stores or reads a secret. Wallet identity comes from the public
        ``AF_WALLET_ADDRESS``, and signing (when a caller wires it up) is done
        by an injected ``signer`` callable that owns the key itself.
        """
        if private_key:
            log.warning(
                "connect() was passed a private key; ignoring it. This adapter "
                "never handles secrets — inject a signer instead."
            )
        self.markets.refresh(force=True)
        self.resolve_account()

    def capabilities(self):  # noqa: ANN201 - matches the venue ABC
        from common.venue_adapter import VenueCapabilities

        return VenueCapabilities(
            supports_alo=True,            # orderType 2 = Post-Only
            supports_trigger_orders=True,  # place-stop-orders / place-sl-tp-orders
            supports_builder_fee=True,     # builderCode {integratorId, integratorFee}
            supports_cross_margin=False,   # Aftermath is ISOLATED margin
        )

    # ── Gas ──────────────────────────────────────────────────────

    @property
    def gas(self) -> gasmod.GasConfig:
        """The active gas decision, resolving a sponsor lazily when needed."""
        if self._gas.mode == "sponsored" and self._gas.sponsor is None and self.config.wallet_address:
            sponsor = gasmod.resolve_sponsor(self.config.wallet_address)
            if sponsor:
                self._gas = gasmod.GasConfig(
                    mode=self._gas.mode,
                    budget_mist=self._gas.budget_mist,
                    sponsor=sponsor,
                    gas_coin_type=self._gas.gas_coin_type,
                )
        return self._gas

    # ── Account discovery ────────────────────────────────────────

    def resolve_account(self, refresh: bool = False) -> AccountRef:
        """Discover the perpetuals account owned by the configured wallet.

        Turnkey requirement: the user supplies a wallet and nothing else -- no
        account id, no capability object id, no collateral coin type.

        ``/api/perpetuals/accounts/owned`` is POST, requires ``walletAddress``
        and returns ``{"accountCaps": [...]}``.
        """
        if self._account is not None and not refresh:
            return self._account

        wallet = self.config.wallet_address
        if not wallet:
            raise RuntimeError(
                "AF_WALLET_ADDRESS is not set — cannot discover the perpetuals account. "
                "Run `nunchi doctor` for a full preflight."
            )

        res = api.post(
            "/api/perpetuals/accounts/owned",
            {"walletAddress": wallet, "collateralCoinTypes": [self.config.collateral_coin_type]},
        )
        caps = (res or {}).get("accountCaps") if isinstance(res, dict) else None
        if not caps:
            raise RuntimeError(
                f"wallet {wallet[:10]}… owns no Aftermath perpetuals account. "
                "Create one with /api/perpetuals/transactions/create-account "
                "(see `nunchi doctor` for guidance)."
            )

        cap = caps[0]
        self._account = AccountRef(
            account_id=NativeAccountId(coerce_bigint(cap["accountId"])),
            cap_id=AccountCapId(cap["objectId"]) if cap.get("objectId") else None,
            collateral_coin_type=str(cap.get("collateralCoinType", self.config.collateral_coin_type)),
            collateral=int(coerce_bigint(cap.get("collateral", 0))),
            raw=cap,
        )
        log.info("resolved Aftermath account %s", self._account.account_id.wire())
        return self._account

    @property
    def account(self) -> AccountRef:
        return self.resolve_account()

    def _account_body(self) -> Dict[str, Any]:
        """The ``PerpetualsAccountOrVaultId`` fragment every write shares.

        ``accountId`` MUST go out as the BigInt string ``"123n"``. A plain JSON
        number is rejected.
        """
        acc = self.account
        body: Dict[str, Any] = {"accountId": to_native_bigint(acc.account_id)}
        if acc.cap_id is not None:
            body["accountCapId"] = str(acc.cap_id)
        return body

    def _invalidate_state(self) -> None:
        """Drop cached account/position state.

        Called after every fill, cancel, deposit, withdrawal and leverage
        change. State goes stale the instant a transaction lands, and computing
        new risk or new orders off a stale snapshot is how a bot doubles a
        position it believes it already has.
        """
        self._account_state_cache = None
        self._positions_cache = None

    # ── Market data ──────────────────────────────────────────────

    def get_snapshot(self, instrument: str = "") -> MarketSnapshot:
        """Top-of-book snapshot for one instrument."""
        inst = normalise_instrument(instrument or "ETH-AF-PERP")
        try:
            market = self.markets.get(inst)
        except (NoSuchMarket, NoMarketsAvailable) as exc:
            log.warning("snapshot unavailable: %s", exc)
            return MarketSnapshot(instrument=inst, timestamp_ms=int(time.time() * 1000))

        book = self._orderbook(market)
        bid = float(book.get("bestBidPrice") or 0.0)
        ask = float(book.get("bestAskPrice") or 0.0)
        mid = float(book.get("midPrice") or 0.0)
        if mid <= 0 and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
        if mid <= 0:
            mid = market.index_price

        spread_bps = ((ask - bid) / mid * 10_000.0) if (mid > 0 and bid > 0 and ask > 0) else 0.0

        return MarketSnapshot(
            instrument=inst,
            mid_price=mid,
            bid=bid,
            ask=ask,
            spread_bps=spread_bps,
            timestamp_ms=int(time.time() * 1000),
            funding_rate=market.estimated_funding_rate,
            open_interest=market.open_interest,
        )

    def _orderbook(self, market: Market) -> Dict[str, Any]:
        """One market's book. Ordering (bids/asks by order id) is preserved.

        The response nests the book one level deeper than the schema digest
        suggests: ``{"orderbooks": [{"orderbook": {...}}]}``. Verified against
        the live API -- reading ``orderbooks[0]`` directly yields a wrapper with
        no price fields, and every quote would silently fall back to the index
        price. Both shapes are accepted so a future flattening does not break.
        """
        try:
            res = api.post(
                "/api/perpetuals/markets/orderbooks",
                {"marketIds": [str(market.market_id)]},
            )
        except api.AfApiError as exc:
            log.warning("orderbook fetch failed for %s: %s", market.symbol, exc)
            return {}
        books = (res or {}).get("orderbooks") if isinstance(res, dict) else None
        if not books:
            return {}
        entry = books[0]
        if isinstance(entry, dict) and isinstance(entry.get("orderbook"), dict):
            return entry["orderbook"]
        return entry if isinstance(entry, dict) else {}

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        """Historical candles.

        v3.0.0 renamed the interval parameter: the native route takes
        ``resolution`` (a CCXT-style string such as ``"1m"``), NOT ``intervalMs``
        or ``interval_ms``. The dedicated candle WebSocket route was removed
        entirely; streaming candles now arrive on the ``marketCandles``
        subscription of ``/api/perpetuals/ws/updates``.
        """
        try:
            market = self.markets.get(coin)
        except (NoSuchMarket, NoMarketsAvailable) as exc:
            log.warning("candles unavailable: %s", exc)
            return []

        now_ms = int(time.time() * 1000)
        res = api.post(
            "/api/perpetuals/market/candle-history",
            {
                "marketId": str(market.market_id),
                "resolution": _resolution(interval),
                "fromTimestamp": max(0, now_ms - int(lookback_ms)),
                "toTimestamp": now_ms,
            },
        )
        candles = (res or {}).get("candles") if isinstance(res, dict) else None
        out: List[Dict] = []
        for c in candles or []:
            out.append(
                {
                    "t": c.get("timestamp"),
                    "T": c.get("timestamp"),
                    "o": str(c.get("open", 0)),
                    "h": str(c.get("high", 0)),
                    "l": str(c.get("low", 0)),
                    "c": str(c.get("close", 0)),
                    "v": str(c.get("volume", 0)),
                    "s": base_asset(coin),
                }
            )
        return out

    def get_all_markets(self) -> Any:
        """Every market, in the API's guaranteed symbol order."""
        markets = self.markets.all()
        return [
            {
                "name": m.symbol,
                "instrument": m.instrument,
                "marketId": str(m.market_id),
                "maxLeverage": (1.0 / m.margin_ratio_initial) if m.margin_ratio_initial > 0 else 0.0,
                "marginRatioInitial": m.margin_ratio_initial,
                "marginRatioMaintenance": m.margin_ratio_maintenance,
                "makerFee": m.maker_fee,
                "takerFee": m.taker_fee,
                "priorityTakerFee": m.priority_taker_fee,
                "minOrderUsdValue": m.min_order_usd_value,
                "openInterest": m.open_interest,
                "fundingRate": m.estimated_funding_rate,
            }
            for m in markets
        ]

    def get_all_mids(self) -> Dict[str, str]:
        """Index price per base asset. Empty pre-relaunch, which is expected."""
        return {m.symbol: str(m.index_price) for m in self.markets.all()}

    # ── Account state ────────────────────────────────────────────

    def get_account_state(self) -> Dict:
        """Account collateral and positions.

        Returned in the ``marginSummary``/``assetPositions`` envelope the engine
        already reads, so no engine change is needed. Note that Aftermath is
        ISOLATED margin: the ``crossMarginSummary`` key is deliberately absent,
        and any consumer assuming cross-margin is wrong about this venue.
        """
        if self._account_state_cache is not None:
            return self._account_state_cache

        acc = self.account
        res = api.post(
            "/api/perpetuals/accounts/positions",
            {"accountIds": [to_native_bigint(acc.account_id)]},
        )
        accounts = (res or {}).get("accounts") if isinstance(res, dict) else None
        rec = accounts[0] if accounts else {}

        positions = rec.get("positions") or []
        asset_positions = []
        for p in positions:
            # Ordering is guaranteed by market id -- preserved, not re-sorted.
            qty = float(p.get("baseAssetAmount") or 0.0)
            asset_positions.append(
                {
                    "position": {
                        "coin": self._symbol_for(p.get("marketId")),
                        "szi": str(qty),
                        "entryPx": str(p.get("entryPrice") or 0),
                        "positionValue": str(p.get("quoteAssetNotionalAmount") or 0),
                        "unrealizedPnl": str(p.get("unrealizedPnlUsd") or 0),
                        "marginUsed": str(p.get("collateralUsd") or 0),
                        "liquidationPx": str(p.get("liquidationPrice") or 0),
                        "leverage": {"type": "isolated", "value": p.get("leverage") or 0},
                        "marginRatio": p.get("marginRatio"),
                        "marketId": p.get("marketId"),
                        # NOTE: v3.0.0 removed per-position makerFee/takerFee.
                        # They are not read here; market-level fees are on the
                        # Market record instead.
                    }
                }
            )

        available = float(rec.get("availableCollateralUsd") or rec.get("availableCollateral") or 0.0)
        state = {
            "marginSummary": {
                "accountValue": str(available),
                "totalMarginUsed": str(sum(float(p.get("collateralUsd") or 0) for p in positions)),
            },
            "assetPositions": asset_positions,
            "withdrawable": str(available),
            "accountId": acc.account_id.wire(),
        }
        self._account_state_cache = state
        self._positions_cache = rec
        return state

    def _symbol_for(self, market_id: Optional[str]) -> str:
        if not market_id:
            return ""
        for m in self.markets.all():
            if str(m.market_id) == str(market_id):
                return m.symbol
        return str(market_id)

    def _position_for(self, instrument: str) -> Optional[Dict[str, Any]]:
        self.get_account_state()
        rec = self._positions_cache or {}
        try:
            target = str(self.markets.market_id(instrument))
        except (NoSuchMarket, NoMarketsAvailable):
            return None
        for p in rec.get("positions") or []:
            if str(p.get("marketId")) == target:
                return p
        return None

    def has_position(self, instrument: str) -> bool:
        """Whether an open position exists.

        Every order-placing route requires ``hasPosition``; getting it wrong
        makes the builder mis-compute the collateral delta.
        """
        p = self._position_for(instrument)
        return bool(p and float(p.get("baseAssetAmount") or 0.0) != 0.0)

    def margin_health(self, instrument: str):
        """Margin health for a position, or None when there is no position."""
        p = self._position_for(instrument)
        if not p:
            return None
        market = self.markets.get(instrument)
        return assess_margin_health(
            float(p.get("marginRatio") or 0.0),
            market.margin_ratio_maintenance,
        )

    # ── Collateral (isolated margin) ─────────────────────────────

    def allocate_collateral(self, instrument: str, amount: float) -> Optional[str]:
        """Allocate collateral to a market's isolated position.

        Unallocated account collateral protects nothing on Aftermath. This is
        the step that turns deposited USDC into margin backing a position, and
        the adapter performs it so strategies never have to know about it.
        """
        market = self.markets.get(instrument)
        body = {
            **self._account_body(),
            "marketId": str(market.market_id),
            "allocateAmount": to_native_bigint(scale_size(amount, market)),
            "walletAddress": self.config.wallet_address,
        }
        tx = build_gated(
            build_path="/api/perpetuals/account/transactions/allocate-collateral",
            body=body,
            gas=self.gas,
            expectation=self._expect(f"allocate {amount} collateral to {market.symbol}"),
            # No preview counterpart exists for allocate-collateral in the spec.
            preview_path=None,
        )
        digest = self._submit(tx)
        if digest:
            self._allocated[normalise_instrument(instrument)] = amount
            self._invalidate_state()
        return digest

    def _ensure_collateral_allocated(self, instrument: str, tif: str = "Ioc") -> None:
        """Allocate margin before a first order in a market, if none is allocated.

        Handled inside the adapter on purpose: strategies were written for a
        cross-margin venue and would otherwise place orders against collateral
        that is not backing anything.
        """
        inst = normalise_instrument(instrument)
        if inst in self._allocated or self.has_position(inst):
            return
        state = self.get_account_state()
        available = float(state["marginSummary"]["accountValue"] or 0.0)
        if available <= 0:
            log.warning("no unallocated collateral available for %s", inst)
            return
        try:
            self.allocate_collateral(inst, available)
        except (NotArmedError, TxInspectionError) as exc:
            log.info("collateral allocation not performed: %s", exc)

    # ── Orders ───────────────────────────────────────────────────

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[Dict] = None,
    ) -> Optional[AfFill]:
        """Place one limit order.

        ``tif`` follows the engine's vocabulary: ``Gtc``/``Ioc``/``Alo``.
        ``Alo`` is maker-only and maps to the API's Post-Only (orderType 2) --
        the mapping matters, because a market-making quote that silently
        becomes a taker order inverts the strategy's fee economics.
        """
        try:
            market = self.markets.get(instrument)
        except (NoSuchMarket, NoMarketsAvailable) as exc:
            log.warning("cannot place order: %s", exc)
            return None

        halt = self.breaker.evaluate(self._bot_state(instrument))
        if halt:
            log.error("order blocked by circuit breaker: %s", halt)
            return None

        with _write_lock:
            self._ensure_collateral_allocated(instrument, tif)

            body = {
                **self._account_body(),
                "walletAddress": self.config.wallet_address,
                "marketId": str(market.market_id),
                "side": side_to_int(side),
                "size": to_native_bigint(scale_size(size, market)),
                "price": to_native_bigint(scale_price(price, market)),
                "orderType": order_type_from_tif(tif),
                "reduceOnly": False,
                "hasPosition": self.has_position(instrument),
                "collateralChange": 0.0,
                "leverage": self.config.leverage,
            }
            bc = builder or self.config.builder_code()
            if bc:
                body["builderCode"] = _normalise_builder_code(bc)

            try:
                tx = build_gated(
                    preview_path="/api/perpetuals/account/previews/place-limit-order",
                    build_path="/api/perpetuals/account/transactions/place-limit-order",
                    body=body,
                    gas=self.gas,
                    expectation=self._expect(
                        f"place {side} {size} {market.symbol} @ {price} ({tif})",
                        market,
                    ),
                )
            except (TxInspectionError, api.AfApiError) as exc:
                log.error("place_order rejected before signing: %s", exc)
                return None

            digest = self._submit(tx)
            if not digest:
                return None

            self._invalidate_state()
            time.sleep(self.config.settle_ms / 1000.0)

        return AfFill(
            oid=digest,
            instrument=normalise_instrument(instrument),
            side=str(side).lower(),
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
        """Atomically cancel and re-place in ONE transaction.

        This is the DEFAULT requote path for every market-making strategy. A
        split cancel-then-place leaves the strategy either unquoted or
        double-quoted in the window between the two transactions, and can fail
        halfway and leave it there.

        ``orderIdsToCancel`` are u64s in BigInt ``"12345n"`` form.
        ``shouldAbortOnMissingId`` is left false so an order that filled between
        the read and the cancel does not abort the whole requote -- that race is
        normal, not exceptional.
        """
        if not cancel_oids and not new_orders:
            return []

        try:
            market = self.markets.get(instrument)
        except (NoSuchMarket, NoMarketsAvailable) as exc:
            log.warning("cannot requote: %s", exc)
            return [None] * len(new_orders)

        orders_to_place = []
        for o in new_orders:
            orders_to_place.append(
                {
                    "side": side_to_int(o.get("side", "buy")),
                    "size": to_native_bigint(scale_size(float(o.get("size", 0)), market)),
                    "price": to_native_bigint(scale_price(float(o.get("price", 0)), market)),
                }
            )

        body = {
            **self._account_body(),
            "walletAddress": self.config.wallet_address,
            "marketId": str(market.market_id),
            "orderIdsToCancel": [to_native_bigint(coerce_bigint(o)) for o in cancel_oids if _is_numeric_oid(o)],
            "ordersToPlace": orders_to_place,
            "orderType": order_type_from_tif(str(new_orders[0].get("tif", "Alo")) if new_orders else "Alo"),
            "reduceOnly": False,
            "hasPosition": self.has_position(instrument),
            "shouldAbortOnMissingId": False,
            "shouldDeallocateFreeCollateral": False,
            "leverage": self.config.leverage,
        }
        bc = self.config.builder_code()
        if bc:
            body["builderCode"] = _normalise_builder_code(bc)

        with _write_lock:
            try:
                tx = build_gated(
                    # cancel-and-place has no dedicated preview route; the
                    # cancel-orders preview covers the cancel leg only, and
                    # gating a combined operation on half of it would be
                    # misleading. Inspection still applies.
                    build_path="/api/perpetuals/account/transactions/cancel-and-place-orders",
                    body=body,
                    gas=self.gas,
                    expectation=self._expect(
                        f"requote {market.symbol}: cancel {len(cancel_oids)}, place {len(orders_to_place)}",
                        market,
                    ),
                )
            except (TxInspectionError, api.AfApiError) as exc:
                log.error("cancel_and_place rejected before signing: %s", exc)
                return [None] * len(new_orders)

            digest = self._submit(tx)
            if not digest:
                return [None] * len(new_orders)
            self._invalidate_state()
            time.sleep(self.config.settle_ms / 1000.0)

        now = int(time.time() * 1000)
        return [
            AfFill(
                oid=digest,
                instrument=normalise_instrument(instrument),
                side=str(o.get("side", "buy")).lower(),
                price=Decimal(str(o.get("price", 0))),
                quantity=Decimal(str(o.get("size", 0))),
                timestamp_ms=now,
            )
            for o in new_orders
        ]

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
        """Place a whole price ladder in ONE transaction.

        Used by grid strategies. N separate placements is the shallow port: it
        costs N gas payments and can leave a partially built grid if one leg
        fails.
        """
        market = self.markets.get(instrument)
        body = {
            **self._account_body(),
            "walletAddress": self.config.wallet_address,
            "marketId": str(market.market_id),
            "side": side_to_int(side),
            "totalSize": to_native_bigint(scale_size(total_size, market)),
            "startPrice": to_native_bigint(scale_price(start_price, market)),
            "endPrice": to_native_bigint(scale_price(end_price, market)),
            "numberOfOrders": int(num_orders),
            "orderType": order_type_from_tif(tif),
            "reduceOnly": False,
            "hasPosition": self.has_position(instrument),
            "collateralChange": 0.0,
            "leverage": self.config.leverage,
        }
        if size_skew is not None:
            body["sizeSkew"] = float(size_skew)

        with _write_lock:
            tx = build_gated(
                preview_path="/api/perpetuals/account/previews/place-scale-order",
                build_path="/api/perpetuals/account/transactions/place-scale-order",
                body=body,
                gas=self.gas,
                expectation=self._expect(
                    f"scale order {market.symbol}: {num_orders} levels {start_price}->{end_price}",
                    market,
                ),
            )
            digest = self._submit(tx)
            if digest:
                self._invalidate_state()
            return digest

    def cancel_order(self, instrument: str, oid: str) -> bool:
        """Cancel one order.

        Prefer :meth:`cancel_and_place_orders` for requotes -- this exists for
        one-off cancellation and shutdown paths.
        """
        return self.cancel_orders(instrument, [oid])

    def cancel_orders(self, instrument: str, oids: Sequence[str]) -> bool:
        """Cancel a batch of orders in one transaction."""
        if not oids:
            return True
        market = self.markets.get(instrument)
        body = {
            **self._account_body(),
            "walletAddress": self.config.wallet_address,
            "marketIdsToData": {
                str(market.market_id): {
                    "orderIds": [to_native_bigint(coerce_bigint(o)) for o in oids if _is_numeric_oid(o)],
                    "collateralChange": 0.0,
                }
            },
            "shouldAbortOnMissingId": False,
        }
        with _write_lock:
            try:
                tx = build_gated(
                    preview_path="/api/perpetuals/account/previews/cancel-orders",
                    build_path="/api/perpetuals/account/transactions/cancel-orders",
                    body=body,
                    gas=self.gas,
                    expectation=self._expect(f"cancel {len(oids)} orders on {market.symbol}", market),
                )
            except (TxInspectionError, api.AfApiError) as exc:
                log.error("cancel rejected before signing: %s", exc)
                return False
            digest = self._submit(tx)
            if digest:
                self._invalidate_state()
            return bool(digest)

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        """Resting orders, read from the position's ``pendingOrders``.

        Ordering (bids then asks, each by order id) is guaranteed by the API
        and preserved rather than re-sorted.
        """
        self.get_account_state()
        rec = self._positions_cache or {}
        want = None
        if instrument:
            try:
                want = str(self.markets.market_id(instrument))
            except (NoSuchMarket, NoMarketsAvailable):
                return []

        out: List[Dict] = []
        for p in rec.get("positions") or []:
            mid = str(p.get("marketId"))
            if want and mid != want:
                continue
            symbol = self._symbol_for(mid)
            for po in p.get("pendingOrders") or []:
                out.append(
                    {
                        "oid": str(coerce_bigint(po.get("orderId", 0))),
                        "coin": symbol,
                        "instrument": f"{symbol}-AF-PERP",
                        "side": "B" if int(po.get("side", 0)) == 0 else "A",
                        "sz": str(po.get("currentSize", 0)),
                        "origSz": str(po.get("initialSize", 0)),
                        "clientOrderId": po.get("clientOrderId"),
                        "marketId": mid,
                    }
                )
        return out

    def cancel_all_verified(self) -> None:
        """Cancel every resting order and VERIFY none survive.

        The kill switch calls this. Verification is the point: a cancel that
        reports success without re-reading the book hides live exposure behind
        a reassuring log line.
        """
        orders = self.get_open_orders()
        if not orders:
            return

        by_instrument: Dict[str, List[str]] = {}
        for o in orders:
            by_instrument.setdefault(o["instrument"], []).append(o["oid"])
        for inst, oids in by_instrument.items():
            try:
                self.cancel_orders(inst, oids)
            except Exception as exc:  # noqa: BLE001 - reported by reconcile below
                log.error("cancel-all failed for %s: %s", inst, exc)

        def _none_left() -> bool:
            self._invalidate_state()
            return not self.get_open_orders()

        reconcile(_none_left, attempts=5, delay_s=0.5, intent="cancel-all")

    # ── Leverage ─────────────────────────────────────────────────

    def set_leverage(self, leverage: int, coin: str = "ETH", is_cross: bool = True) -> None:
        """Set position leverage for a market.

        ``is_cross`` is accepted for signature compatibility and IGNORED:
        Aftermath is isolated-margin only, so there is no cross mode to select.
        A caller asking for cross gets a warning rather than silent divergence
        between what it requested and what it got.
        """
        if is_cross:
            log.debug(
                "set_leverage(is_cross=True) ignored — Aftermath is isolated margin only"
            )
        market = self.markets.get(coin)
        body = {
            **self._account_body(),
            "marketId": str(market.market_id),
            "leverage": float(leverage),
            "collateralChange": 0.0,
            "walletAddress": self.config.wallet_address,
        }
        with _write_lock:
            try:
                tx = build_gated(
                    preview_path="/api/perpetuals/account/previews/set-leverage",
                    build_path="/api/perpetuals/account/transactions/set-leverage",
                    body=body,
                    gas=self.gas,
                    expectation=self._expect(f"set leverage {leverage}x on {market.symbol}", market),
                )
            except (TxInspectionError, api.AfApiError) as exc:
                log.warning("set_leverage not applied: %s", exc)
                return
            if self._submit(tx):
                self.config.leverage = float(leverage)
                self._invalidate_state()

    # ── Trigger / stop orders ────────────────────────────────────

    def place_trigger_order(
        self,
        instrument: str,
        side: str,
        size: float,
        trigger_price: float,
        builder: Optional[Dict] = None,
        is_take_profit: bool = False,
    ) -> Optional[str]:
        """Place a stop-loss or take-profit trigger.

        v3.0.0 renamed these fields: ``stopLossPrice`` / ``takeProfitPrice``,
        NOT ``stopLossIndexPrice`` / ``takeProfitIndexPrice``. The reference
        price is now selected explicitly by ``triggerPriceType``
        (0 index, 1 book mid, 2 mark); mark price is used here because it is
        canonical for liquidation.
        """
        market = self.markets.get(instrument)
        stop_data: Dict[str, Any] = {
            "size": to_native_bigint(scale_size(size, market)),
            "triggerPriceType": TRIGGER_PRICE_MARK,
            "marketId": str(market.market_id),
            "side": side_to_int(side),
        }
        if is_take_profit:
            stop_data["takeProfitPrice"] = float(trigger_price)
        else:
            stop_data["stopLossPrice"] = float(trigger_price)
        bc = builder or self.config.builder_code()
        if bc:
            stop_data["builderCode"] = _normalise_builder_code(bc)

        body = {
            **self._account_body(),
            "walletAddress": self.config.wallet_address,
            "stopOrders": [stop_data],
            "isSponsoredTx": self.gas.mode == "sponsored",
        }
        with _write_lock:
            tx = build_gated(
                build_path="/api/perpetuals/account/transactions/place-stop-orders",
                body=body,
                gas=self.gas,
                expectation=self._expect(
                    f"{'TP' if is_take_profit else 'SL'} {market.symbol} @ {trigger_price}", market
                ),
                preview_path=None,  # no preview counterpart in the spec
            )
            digest = self._submit(tx)
            if digest:
                self._invalidate_state()
            return digest

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        """Cancel a stop/trigger order."""
        market = self.markets.get(instrument)
        body = {
            **self._account_body(),
            "walletAddress": self.config.wallet_address,
            "stopOrderIds": [to_native_bigint(coerce_bigint(oid))] if _is_numeric_oid(oid) else [],
            "marketId": str(market.market_id),
        }
        with _write_lock:
            try:
                tx = build_gated(
                    build_path="/api/perpetuals/account/transactions/cancel-stop-orders",
                    body=body,
                    gas=self.gas,
                    expectation=self._expect(f"cancel stop order {oid} on {market.symbol}", market),
                    preview_path=None,
                )
            except (TxInspectionError, api.AfApiError) as exc:
                log.error("cancel_trigger_order rejected: %s", exc)
                return False
            digest = self._submit(tx)
            if digest:
                self._invalidate_state()
            return bool(digest)

    # ── Guards ───────────────────────────────────────────────────

    def max_order_size(self, instrument: str, side: str, price: Optional[float] = None) -> Optional[float]:
        """Venue-computed maximum order size. Consult before a large order."""
        market = self.markets.get(instrument)
        try:
            res = api.post(
                "/api/perpetuals/account/max-order-size",
                {
                    "accountId": to_native_bigint(self.account.account_id),
                    "marketId": str(market.market_id),
                    "side": side_to_int(side),
                    "price": float(price) if price is not None else None,
                    "leverage": self.config.leverage,
                },
            )
        except api.AfApiError as exc:
            log.warning("max-order-size unavailable: %s", exc)
            return None
        if isinstance(res, dict):
            for key in ("maxOrderSize", "size", "maxSize"):
                if key in res:
                    try:
                        return float(coerce_bigint(res[key]))
                    except (TypeError, ValueError):
                        continue
        return None

    def _bot_state(self, instrument: str) -> BotState:
        """Assemble the circuit breaker's view of the world."""
        state = BotState()
        try:
            acct = self.get_account_state()
            state.position_notional = sum(
                abs(float(ap["position"].get("positionValue") or 0)) for ap in acct.get("assetPositions", [])
            )
            health = self.margin_health(instrument)
            if health is not None:
                state.margin_buffer = health.buffer_multiple
            pos = self._position_for(instrument)
            if pos:
                state.effective_leverage = float(pos.get("leverage") or 0.0)
        except (api.AfApiError, NoSuchMarket, NoMarketsAvailable, ValueError, RuntimeError) as exc:
            log.debug("bot state unavailable: %s", exc)
        return state

    # ── The signing boundary ─────────────────────────────────────

    def _expect(self, intent: str, market: Optional[Market] = None) -> TxExpectation:
        return TxExpectation(
            sender=str(self.config.wallet_address or ""),
            gas=self.gas,
            intent=intent,
            expected_package=market.package_id if market and market.package_id else None,
        )

    def _submit(self, tx: InspectedTx) -> Optional[str]:
        """The single point where a transaction would be signed and submitted.

        Every mutating path funnels through here, so there is exactly one place
        to audit and exactly one place that enforces arming.

        This repository ships with ``self._signer is None`` and ``AF_ARMED``
        false, so this raises :class:`NotArmedError` -- by design. Builds,
        previews and inspections above this line all execute normally, which is
        what makes the pipeline verifiable without ever broadcasting anything.
        """
        if not self.config.armed or self._signer is None:
            raise NotArmedError(
                f"built and inspected '{tx.expectation.intent}' but did not submit: "
                f"armed={self.config.armed}, signer={'wired' if self._signer else 'absent'}. "
                "This build ships disarmed and without a signer."
            )
        raise NotImplementedError(  # pragma: no cover - unreachable while disarmed
            "submission is intentionally not implemented in this build"
        )


def _resolution(interval: str) -> str:
    """Normalise an interval to the CCXT-style ``resolution`` string v3 expects."""
    s = str(interval).strip().lower()
    if s.isdigit():  # legacy milliseconds -- convert rather than send a number
        ms = int(s)
        for unit_ms, label in ((86_400_000, "1d"), (3_600_000, "1h"), (60_000, "1m")):
            if ms >= unit_ms and ms % unit_ms == 0:
                return f"{ms // unit_ms}{label[-1]}"
        return "1m"
    return s


def _is_numeric_oid(oid: Any) -> bool:
    """Order ids are u64s. A transaction digest is not an order id."""
    try:
        coerce_bigint(oid)
        return True
    except TypeError:
        return False


def _normalise_builder_code(bc: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce any legacy builder-code shape to the v3.0.0 field names.

    v3.0.0 renamed ``integratorAddress`` (an address string) to ``integratorId``
    (a u32 NUMBER), and ``takerFee`` to ``integratorFee``. A pre-v3 payload is
    accepted here and translated rather than silently sent and rejected.
    """
    out: Dict[str, Any] = {}
    ident = bc.get("integratorId", bc.get("integratorAddress"))
    if ident is not None:
        try:
            out["integratorId"] = int(ident)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"integratorId must be a u32 number in v3.0.0, got {ident!r}. "
                "The old address-string `integratorAddress` is no longer accepted."
            ) from exc
    fee = bc.get("integratorFee", bc.get("takerFee"))
    if fee is not None:
        out["integratorFee"] = float(fee)
    return out
