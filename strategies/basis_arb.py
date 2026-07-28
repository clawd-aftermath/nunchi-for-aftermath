"""Basis arbitrage strategy — trades implied basis from funding rate."""
from __future__ import annotations

from collections import deque
from typing import List, Optional

from common.models import MarketSnapshot, StrategyDecision, annualize_funding_rate
from sdk.strategy_sdk.base import BaseStrategy, StrategyContext


class BasisArbStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str = "basis_arb",
        basis_threshold_bps: float = 5.0,
        size: float = 1.0,
        funding_window: int = 10,
    ):
        super().__init__(strategy_id=strategy_id)
        self.basis_threshold_bps = basis_threshold_bps
        self.size = size
        self.funding_window = funding_window

        self.funding_history: deque = deque(maxlen=funding_window)

    def on_tick(self, snapshot: MarketSnapshot,
                context: Optional[StrategyContext] = None) -> List[StrategyDecision]:
        mid = snapshot.mid_price
        if mid <= 0:
            return []

        funding = snapshot.funding_rate
        self.funding_history.append(funding)

        if len(self.funding_history) < 3:
            return []

        avg_funding = sum(self.funding_history) / len(self.funding_history)
        try:
            annualized_funding = annualize_funding_rate(
                avg_funding,
                snapshot.funding_interval_hours,
            )
        except ValueError:
            return []
        basis_ann_bps = annualized_funding * 10_000

        ctx = context or StrategyContext()
        orders: List[StrategyDecision] = []

        if abs(basis_ann_bps) < self.basis_threshold_bps:
            return []

        # Positive basis (contango): funding is positive, shorts collect
        # Negative basis (backwardation): funding is negative, longs collect
        if basis_ann_bps > self.basis_threshold_bps:
            # Short to collect positive funding
            if ctx.position_qty <= 0:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=self.size,
                    limit_price=round(snapshot.bid, 2),
                    order_type="Ioc",
                    meta={
                        "signal": "short_contango",
                        "basis_ann_bps": round(basis_ann_bps, 2),
                        "avg_funding": round(avg_funding, 8),
                    },
                ))
            elif ctx.position_qty > 0:
                # Close long — wrong side of funding
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="sell",
                    size=abs(ctx.position_qty),
                    limit_price=round(snapshot.bid, 2),
                    order_type="Ioc",
                    meta={"signal": "close_wrong_side", "basis_ann_bps": round(basis_ann_bps, 2)},
                ))
        elif basis_ann_bps < -self.basis_threshold_bps:
            # Long to collect negative funding (backwardation)
            if ctx.position_qty >= 0:
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=self.size,
                    limit_price=round(snapshot.ask, 2),
                    order_type="Ioc",
                    meta={
                        "signal": "long_backwardation",
                        "basis_ann_bps": round(basis_ann_bps, 2),
                        "avg_funding": round(avg_funding, 8),
                    },
                ))
            elif ctx.position_qty < 0:
                # Close short — wrong side
                orders.append(StrategyDecision(
                    action="place_order",
                    instrument=snapshot.instrument,
                    side="buy",
                    size=abs(ctx.position_qty),
                    limit_price=round(snapshot.ask, 2),
                    order_type="Ioc",
                    meta={"signal": "close_wrong_side", "basis_ann_bps": round(basis_ann_bps, 2)},
                ))

        return orders
