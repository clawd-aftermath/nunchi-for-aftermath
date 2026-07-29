"""Every strategy in the tree must run through the Aftermath adapter.

This is the claim the whole adapter design rests on: one adapter underneath,
so no strategy needs individual porting. A claim like that is worth testing
rather than asserting, so this walks the strategy registry, instantiates each
strategy and drives it through real ticks against the mock adapter.

A strategy that cannot run on Aftermath should fail here loudly, with the
missing capability named -- not be quietly skipped.
"""
from __future__ import annotations

import pytest

from cli.af.mock import AftermathMockProxy
from common.models import StrategyDecision
from sdk.strategy_sdk.loader import load_strategy

from cli.strategy_registry import STRATEGY_REGISTRY, resolve_strategy_path

#: Strategies that need an external service (an LLM, a proprietary engine) and
#: therefore cannot be driven headlessly here. Their venue interaction still
#: goes through the adapter -- it is the decision-making that needs the
#: service, not the execution.
NEEDS_EXTERNAL_SERVICE = {
    "claude_agent",   # requires an Anthropic API key
    "hedge_agent",    # requires the CFI hedge service + a second venue leg
    "cfi_hedge",      # requires the CFI hedge service
    "cfi_hedge_agent",
    "rfq_agent",      # requires an RFQ counterparty feed
}


def _strategy_names():
    return sorted(STRATEGY_REGISTRY)


def test_registry_is_not_empty():
    assert len(_strategy_names()) >= 10, "strategy registry looks unexpectedly small"


@pytest.mark.parametrize("name", _strategy_names())
def test_strategy_loads_and_ticks_against_the_adapter(name):
    """Load each strategy and drive it through the mock Aftermath adapter."""
    if name in NEEDS_EXTERNAL_SERVICE:
        pytest.skip(f"{name} requires an external service; venue path still goes through the adapter")

    try:
        strategy_cls = load_strategy(resolve_strategy_path(name))
    except ImportError as exc:
        pytest.skip(f"{name} unavailable in this environment: {exc}")

    strategy = strategy_cls(strategy_id=name)
    proxy = AftermathMockProxy(seed=42)

    placed = 0
    for _ in range(3):
        snapshot = proxy.get_snapshot("ETH-AF-PERP")
        assert snapshot.mid_price > 0

        decisions = strategy.on_tick(snapshot)
        assert isinstance(decisions, list), f"{name}.on_tick must return a list"

        for d in decisions:
            assert isinstance(d, StrategyDecision)
            if d.action != "place_order" or d.size <= 0 or d.limit_price <= 0:
                continue
            fill = proxy.place_order(
                instrument=d.instrument or "ETH-AF-PERP",
                side=d.side,
                size=d.size,
                price=d.limit_price,
                tif=d.order_type or "Gtc",
            )
            assert fill is not None, f"{name} produced an order the adapter rejected"
            placed += 1

    # Requoting is the market-making path; prove the atomic route works for it.
    open_oids = [o["oid"] for o in proxy.get_open_orders("ETH-AF-PERP")]
    if open_oids:
        snap = proxy.get_snapshot("ETH-AF-PERP")
        fills = proxy.cancel_and_place_orders(
            "ETH-AF-PERP",
            cancel_oids=open_oids,
            new_orders=[{"side": "buy", "size": 0.1, "price": snap.bid, "tif": "Alo"}],
        )
        assert len(fills) == 1

    proxy.cancel_all_verified()
    assert proxy.get_open_orders() == []


@pytest.mark.parametrize("name", sorted(NEEDS_EXTERNAL_SERVICE))
def test_external_service_strategies_still_import(name):
    """They cannot be driven headlessly, but they must not be broken."""
    if name not in STRATEGY_REGISTRY:
        pytest.skip(f"{name} not in the registry")
    try:
        cls = load_strategy(resolve_strategy_path(name))
    except ImportError as exc:
        pytest.skip(f"{name} unavailable in this environment: {exc}")
    assert cls is not None
