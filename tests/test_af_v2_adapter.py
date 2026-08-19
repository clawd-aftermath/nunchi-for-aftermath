"""Adapter contract: interface parity, ID typing, BigInt wire format, gas modes.

The parity test is the load-bearing one. A mock that has drifted from its
subject makes every other green test meaningless, so parity is enforced
mechanically rather than by discipline.
"""
from __future__ import annotations

import inspect

import pytest

from cli.af.mock import AftermathMockProxy
from cli.af.proxy import AftermathProxy

#: The interface every strategy reaches the venue through. Derived from what
#: cli/engine.py and cli/order_manager.py actually call, plus the v1 surface
#: that strategies and commands depend on.
ADAPTER_INTERFACE = [
    "get_snapshot",
    "get_account_state",
    "place_order",
    "cancel_order",
    "cancel_orders",
    "cancel_and_place_orders",
    "place_scale_order",
    "get_open_orders",
    "get_candles",
    "get_all_markets",
    "get_all_mids",
    "set_leverage",
    "allocate_collateral",
    "place_trigger_order",
    "cancel_trigger_order",
    "cancel_all_verified",
    "max_order_size",
    "has_position",
    "margin_health",
    "resolve_account",
    "connect",
    "capabilities",
]


@pytest.mark.parametrize("name", ADAPTER_INTERFACE)
def test_real_adapter_exposes_the_interface(name):
    assert callable(getattr(AftermathProxy, name, None)), f"AftermathProxy is missing {name}()"


@pytest.mark.parametrize("name", ADAPTER_INTERFACE)
def test_mock_adapter_exposes_the_interface(name):
    assert callable(getattr(AftermathMockProxy, name, None)), f"AftermathMockProxy is missing {name}()"


@pytest.mark.parametrize("name", ADAPTER_INTERFACE)
def test_mock_and_real_signatures_match(name):
    """Parity by test, not by discipline."""
    real = inspect.signature(getattr(AftermathProxy, name))
    mock = inspect.signature(getattr(AftermathMockProxy, name))
    assert list(real.parameters) == list(mock.parameters), (
        f"{name}() parameter names diverge:\n  real: {real}\n  mock: {mock}"
    )


def test_engine_and_order_manager_calls_are_all_covered():
    """Everything the engine layer calls on the venue must be in the interface."""
    for required in (
        "get_account_state", "get_snapshot", "place_order", "set_leverage",
        "cancel_and_place_orders", "cancel_order", "get_open_orders",
    ):
        assert required in ADAPTER_INTERFACE


# ── ID typing ────────────────────────────────────────────────────


def test_account_identities_are_not_interchangeable():
    """Mixing account identities is the most common failure against this API."""
    from cli.af.ids import AccountCapId, AccountNumber, NativeAccountId

    native = NativeAccountId(123)
    cap = AccountCapId("0x" + "ab" * 32)
    num = AccountNumber(7)

    assert native != cap and cap != num and native != num
    # A capability object id is not a native account id.
    with pytest.raises(TypeError):
        NativeAccountId("0xdeadbeef")
    # A native id is not a capability object id.
    with pytest.raises(TypeError):
        AccountCapId("123")
    with pytest.raises(TypeError):
        AccountNumber("7")


def test_market_id_rejects_tickers():
    """`marketId` is not a ticker; the API validates them strictly."""
    from cli.af.ids import MarketId

    with pytest.raises(TypeError, match="not market ids"):
        MarketId("BTC")
    assert str(MarketId("0x" + "11" * 32)).startswith("0x")


def test_native_bigint_wire_format():
    """gotcha 11: native BigInt fields use the exact `"123n"` string."""
    from cli.af.ids import NativeAccountId, from_native_bigint, is_native_bigint, to_native_bigint

    assert to_native_bigint(123) == "123n"
    assert to_native_bigint(NativeAccountId(9)) == "9n"
    assert from_native_bigint("456n") == 456
    assert is_native_bigint("1n") and not is_native_bigint("1")

    # A plain number is NOT acceptable on the wire.
    with pytest.raises(TypeError):
        from_native_bigint("789")


def test_side_and_order_type_encodings():
    from cli.af.ids import ORDER_TYPE_IOC, ORDER_TYPE_POST_ONLY, order_type_from_tif, side_to_int

    assert side_to_int("buy") == 0 and side_to_int("sell") == 1
    # Alo is maker-only; mapping it to a taker type would invert MM economics.
    assert order_type_from_tif("Alo") == ORDER_TYPE_POST_ONLY == 2
    assert order_type_from_tif("Ioc") == ORDER_TYPE_IOC == 3
    assert order_type_from_tif("Gtc") == 0
    with pytest.raises(ValueError):
        order_type_from_tif("nonsense")


# ── Gas modes ────────────────────────────────────────────────────


def test_gas_modes_are_the_documented_three():
    from cli.af.gas import GAS_MODES, parse_gas_mode

    assert set(GAS_MODES) == {"sponsored", "self", "dynamic"}
    assert parse_gas_mode(None) == "sponsored"  # zero-friction default
    assert parse_gas_mode("dynamic") == "dynamic"


def test_unknown_gas_mode_fails_loudly_never_silently():
    """A typo must not silently change who pays for gas."""
    from cli.af.gas import parse_gas_mode

    with pytest.raises(ValueError, match="invalid AF_GAS_MODE"):
        parse_gas_mode("sponsered")


def test_gas_budget_is_always_explicit():
    from cli.af.gas import GasConfig, apply_gas_to_body

    with pytest.raises(ValueError, match="positive and explicit"):
        GasConfig(mode="self", budget_mist=0)

    body = apply_gas_to_body({"marketId": "0x1"}, GasConfig(mode="self", budget_mist=5))
    assert body["gasBudget"] == "5"
    assert "sponsor" not in body


def test_sponsor_is_an_object_not_a_bare_string():
    """The spec models sponsorship as `sponsor: {walletAddress}`."""
    from cli.af.gas import GasConfig, apply_gas_to_body

    addr = "0x" + "cd" * 32
    body = apply_gas_to_body({}, GasConfig(mode="sponsored", sponsor=addr))
    assert body["sponsor"] == {"walletAddress": addr}
    assert body["isSponsoredTx"] is True


def test_apply_gas_does_not_mutate_the_callers_body():
    from cli.af.gas import GasConfig, apply_gas_to_body

    original = {"marketId": "0x1"}
    apply_gas_to_body(original, GasConfig(mode="self"))
    assert original == {"marketId": "0x1"}


# ── Instrument normalisation (HL semantics removed) ───────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ETH", "ETH-AF-PERP"),
        ("eth-perp", "ETH-AF-PERP"),
        ("ETH-AF-PERP", "ETH-AF-PERP"),
        ("BTC/USDC:USDC", "BTC-AF-PERP"),
        ("xyz:BTC-PERP", "BTC-AF-PERP"),  # HL HIP-3 prefix stripped, not translated
    ],
)
def test_instrument_normalisation(raw, expected):
    from cli.af.markets import normalise_instrument

    assert normalise_instrument(raw) == expected


# ── The mock runs offline ────────────────────────────────────────


def test_mock_needs_no_network_and_no_keys():
    m = AftermathMockProxy(seed=1)
    snap = m.get_snapshot("ETH")
    assert snap.mid_price > 0 and snap.bid < snap.ask
    assert m.get_all_mids()
    assert len(m.get_candles("ETH", "1m", 600_000)) > 0

    fill = m.place_order("ETH", "buy", 1.0, snap.bid, tif="Alo")
    assert fill is not None
    assert len(m.get_open_orders("ETH")) == 1

    m.cancel_all_verified()
    assert m.get_open_orders("ETH") == []


def test_mock_cancel_and_place_is_atomic_in_effect():
    m = AftermathMockProxy(seed=2)
    snap = m.get_snapshot("ETH")
    first = m.place_order("ETH", "buy", 1.0, snap.bid, tif="Alo")
    assert first is not None

    fills = m.cancel_and_place_orders(
        "ETH",
        cancel_oids=[first.oid],
        new_orders=[
            {"side": "buy", "size": 1.0, "price": snap.bid * 0.999, "tif": "Alo"},
            {"side": "sell", "size": 1.0, "price": snap.ask * 1.001, "tif": "Alo"},
        ],
    )
    assert len(fills) == 2
    open_orders = m.get_open_orders("ETH")
    assert len(open_orders) == 2
    assert first.oid not in [o["oid"] for o in open_orders]


def test_aftermath_is_isolated_margin_not_cross():
    """Any code assuming cross-margin is wrong about this venue."""
    caps = AftermathMockProxy().capabilities()
    assert caps.supports_cross_margin is False
    assert caps.supports_alo is True


def test_account_state_has_no_cross_margin_summary():
    state = AftermathMockProxy().get_account_state()
    assert "crossMarginSummary" not in state
    assert "marginSummary" in state


# ── Legacy import path still works ───────────────────────────────


def test_legacy_af_proxy_imports_still_resolve():
    from cli.af_proxy import AftermathMockProxy as M, AftermathProxy as P, _normalise_instrument

    assert P is AftermathProxy and M is AftermathMockProxy
    assert _normalise_instrument("eth") == "ETH-AF-PERP"


# ── Wire encoding, sampled from production BTCUSD (2026-08-19) ──
# This is an offline fixture, not a claim that mutable prices or fees remain
# current. It preserves the production object/collateral IDs and wire shapes
# that exposed the covered bugs without requiring network access in the suite.


def _production_market_fixture():
    """A fixed production BTCUSD sample, including BigInt strings."""
    from cli.af.markets import parse_market

    return parse_market(
        {
            "objectId": "0x05b5c3bea84c4b8f33cf592d899008336dcbae8c9c6c75b2f8e7b8f7878744c1",
            "packageId": "0x3ec740df8428aa9c93aaef7f8cc1542ac3194fd014826b51bfe245346d64efc7",
            "collateralCoinType": (
                "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7"
                "::usdc::USDC"
            ),
            "indexPrice": 68702.18975195,
            "collateralPrice": 0.99988992,
            "estimatedFundingRate": -1.32828440761275e-05,
            "nextFundingTimestampMs": "1787173200000n",
            "marketParams": {
                "baseAssetSymbol": "BTCUSD",
                # These arrive as BigInt "…n" STRINGS on the wire.
                "lotSize": "1n",
                "tickSize": "1000000n",
                "maxPendingOrders": "80n",
                "scalingFactor": 1e-06,
                "marginRatioInitial": 0.05,
                "marginRatioMaintenance": 0.025,
                "makerFee": -0.00005,
                "takerFee": 0.00045,
                "priorityTakerFee": 0.001,
                "minOrderUsdValue": 1.0,
            },
            "marketState": {"openInterest": 0.00026823},
        }
    )


def test_bigint_response_fields_are_decoded_not_defaulted():
    """`tickSize` arrives as "1000000n"; int() raises and a default would be silent.

    Defaulting tickSize to 1 rounds every order price to the wrong grid, and
    the rejection happens on-chain rather than here.
    """
    m = _production_market_fixture()
    assert m.tick_size == 1_000_000, "tickSize must be decoded from its BigInt string form"
    assert m.lot_size == 1
    assert m.max_pending_orders == 80


def test_price_and_size_use_the_1e9_fixed_point_not_scaling_factor():
    """Verified 12/12 against live orderbook levels.

    `scalingFactor` (1e-06 here) converts COLLATERAL units. Using it for price
    would scale a $64,108 order down to zero.
    """
    from cli.af.markets import FIXED_POINT, scale_price, scale_size, unscale

    m = _production_market_fixture()
    assert FIXED_POINT == 10**9
    assert m.scaling_factor == 1e-06  # present, but deliberately not used here

    raw_px = scale_price(64108.3049, m)
    assert raw_px == 64_108_304_000_000
    assert raw_px % m.tick_size == 0
    assert unscale(raw_px) == pytest.approx(64108.3049)

    raw_sz = scale_size(0.000026014, m)
    assert raw_sz == 26_014
    assert raw_sz % m.lot_size == 0


def test_prices_snap_down_to_the_tick_grid():
    from cli.af.markets import scale_price

    m = _production_market_fixture()
    # A price between ticks must land on the grid, never between it.
    assert scale_price(64108.30491234, m) % m.tick_size == 0


def test_symbol_aliases_resolve_a_glued_quote_currency():
    """The live market reports `baseAssetSymbol == "BTCUSD"`, not "BTC"."""
    from cli.af.markets import symbol_aliases

    assert symbol_aliases("BTCUSD") == ["BTCUSD", "BTC"]
    assert symbol_aliases("ETH") == ["ETH"]
    assert symbol_aliases("SUIUSDC") == ["SUIUSDC", "SUI"]


def test_orderbook_response_is_nested_one_level_deeper():
    """Live shape is {"orderbooks":[{"orderbook":{...}}]}.

    Reading orderbooks[0] directly yields a wrapper with no price fields, and
    every quote would silently fall back to the index price.
    """
    from cli.af.config import AfConfig
    from cli.af.proxy import AftermathProxy
    from cli.af import api as apimod

    proxy = AftermathProxy(AfConfig(wallet_address="0x" + "11" * 32))
    m = _production_market_fixture()

    nested = {"orderbooks": [{"orderbook": {"bestBidPrice": 1.0, "bestAskPrice": 2.0, "midPrice": 1.5}}]}
    flat = {"orderbooks": [{"bestBidPrice": 1.0, "bestAskPrice": 2.0, "midPrice": 1.5}]}

    for shape in (nested, flat):
        orig = apimod.post
        try:
            apimod.post = lambda *a, **k: shape
            book = proxy._orderbook(m)
        finally:
            apimod.post = orig
        assert book.get("midPrice") == 1.5, f"failed to read book from {shape!r}"
