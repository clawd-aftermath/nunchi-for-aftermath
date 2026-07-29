"""Position tracking — per-agent and aggregate House inventory.

Tracks net position, average entry price, realized PnL, and notional
exposure across clearing rounds.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

log = logging.getLogger("position_tracker")
ZERO = Decimal("0")


@dataclass
class Position:
    """Net position for a single instrument."""

    instrument: str
    net_qty: Decimal = ZERO           # positive = long, negative = short
    avg_entry_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    total_buy_qty: Decimal = ZERO
    total_sell_qty: Decimal = ZERO
    num_fills: int = 0

    @property
    def notional(self) -> Decimal:
        """Absolute notional exposure at average entry."""
        return abs(self.net_qty * self.avg_entry_price)

    def apply_fill(self, side: str, qty: Decimal, price: Decimal) -> None:
        """Update position from a single fill."""
        if qty <= ZERO:
            return

        signed_qty = qty if side == "buy" else -qty
        old_qty = self.net_qty
        new_qty = old_qty + signed_qty

        # Realized PnL when reducing position
        if old_qty != ZERO and self.avg_entry_price > ZERO:
            is_reducing = (old_qty > ZERO and signed_qty < ZERO) or \
                          (old_qty < ZERO and signed_qty > ZERO)
            if is_reducing:
                closed_qty = min(abs(signed_qty), abs(old_qty))
                if old_qty > ZERO:
                    # Was long, selling to reduce
                    self.realized_pnl += (price - self.avg_entry_price) * closed_qty
                else:
                    # Was short, buying to reduce
                    self.realized_pnl += (self.avg_entry_price - price) * closed_qty

        # Update average entry price
        if new_qty == ZERO:
            # Flat — keep avg_entry for reference but position is closed
            pass
        elif old_qty == ZERO:
            # Opening fresh position
            self.avg_entry_price = price
        elif (old_qty > ZERO) != (new_qty > ZERO):
            # Flipped sides — new entry at current price
            self.avg_entry_price = price
        elif abs(new_qty) > abs(old_qty):
            # Adding to existing position — weighted average
            total_cost = self.avg_entry_price * abs(old_qty) + price * qty
            self.avg_entry_price = total_cost / abs(new_qty)
        # If reducing but not flat/flipped, avg_entry stays the same

        self.net_qty = new_qty
        self.num_fills += 1
        if side == "buy":
            self.total_buy_qty += qty
        else:
            self.total_sell_qty += qty

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        """Unrealized PnL at a given mark price."""
        if self.net_qty == ZERO or self.avg_entry_price == ZERO:
            return ZERO
        if self.net_qty > ZERO:
            return (mark_price - self.avg_entry_price) * self.net_qty
        else:
            return (self.avg_entry_price - mark_price) * abs(self.net_qty)

    def total_pnl(self, mark_price: Decimal) -> Decimal:
        """Total PnL (realized + unrealized)."""
        return self.realized_pnl + self.unrealized_pnl(mark_price)

    def to_dict(self, mark_price: Optional[Decimal] = None) -> Dict[str, Any]:
        d = {
            "instrument": self.instrument,
            "net_qty": str(self.net_qty),
            "avg_entry_price": str(self.avg_entry_price),
            "realized_pnl": str(self.realized_pnl),
            "notional": str(self.notional),
            "total_buy_qty": str(self.total_buy_qty),
            "total_sell_qty": str(self.total_sell_qty),
            "num_fills": self.num_fills,
        }
        if mark_price is not None:
            d["unrealized_pnl"] = str(self.unrealized_pnl(mark_price))
            d["total_pnl"] = str(self.total_pnl(mark_price))
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        return cls(
            instrument=data["instrument"],
            net_qty=Decimal(data["net_qty"]),
            avg_entry_price=Decimal(data["avg_entry_price"]),
            realized_pnl=Decimal(data["realized_pnl"]),
            total_buy_qty=Decimal(data.get("total_buy_qty", "0")),
            total_sell_qty=Decimal(data.get("total_sell_qty", "0")),
            num_fills=data.get("num_fills", 0),
        )


class PositionTracker:
    """Tracks positions for all agents and the aggregate House."""

    def __init__(self):
        self.agent_positions: Dict[str, Dict[str, Position]] = defaultdict(dict)
        self.house_positions: Dict[str, Position] = {}

    def apply_fill(self, agent_id: str, instrument: str, side: str,
                   qty: Decimal, price: Decimal) -> None:
        """Apply a single fill to both agent and house positions."""
        # Agent position
        if instrument not in self.agent_positions[agent_id]:
            self.agent_positions[agent_id][instrument] = Position(instrument=instrument)
        self.agent_positions[agent_id][instrument].apply_fill(side, qty, price)

        # House aggregate
        if instrument not in self.house_positions:
            self.house_positions[instrument] = Position(instrument=instrument)
        self.house_positions[instrument].apply_fill(side, qty, price)

    def apply_clearing_fills(self, fills: List[Dict]) -> None:
        """Bulk update from clearing result fills."""
        for f in fills:
            qty = Decimal(str(f.get("quantity_filled", "0")))
            if qty <= ZERO:
                continue
            self.apply_fill(
                f["agent_id"], f["instrument"], f["side"],
                qty, Decimal(str(f["fill_price"])),
            )

    def get_house_position(self, instrument: str) -> Position:
        return self.house_positions.get(instrument, Position(instrument=instrument))

    def get_agent_position(self, agent_id: str, instrument: str) -> Position:
        return self.agent_positions.get(agent_id, {}).get(
            instrument, Position(instrument=instrument),
        )

    def get_house_inventory(self, instrument: str) -> Decimal:
        """Net quantity for the house (positive=long, negative=short)."""
        return self.get_house_position(instrument).net_qty

    def get_wallet_positions(self, wallet_id: str) -> Dict[str, Position]:
        """Get all positions for a specific wallet (agent_id == wallet_id)."""
        return dict(self.agent_positions.get(wallet_id, {}))

    def get_wallet_pnl(self, wallet_id: str, mark_prices: Dict[str, Decimal]) -> Decimal:
        """Total PnL (realized + unrealized) for a specific wallet."""
        total = ZERO
        for inst, pos in self.agent_positions.get(wallet_id, {}).items():
            mp = mark_prices.get(inst, pos.avg_entry_price)
            total += pos.total_pnl(mp)
        return total

    def get_all_instruments(self) -> List[str]:
        return list(self.house_positions.keys())

    def snapshot(self, mark_prices: Optional[Dict[str, Decimal]] = None) -> Dict[str, Any]:
        """Full snapshot of all positions."""
        mark_prices = mark_prices or {}
        return {
            "house": {
                inst: pos.to_dict(mark_prices.get(inst))
                for inst, pos in self.house_positions.items()
            },
            "agents": {
                sid: {
                    inst: pos.to_dict(mark_prices.get(inst))
                    for inst, pos in instruments.items()
                }
                for sid, instruments in self.agent_positions.items()
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            "house": {inst: pos.to_dict() for inst, pos in self.house_positions.items()},
            "agents": {
                sid: {inst: pos.to_dict() for inst, pos in instruments.items()}
                for sid, instruments in self.agent_positions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionTracker":
        """Restore from persisted state."""
        tracker = cls()
        for inst, pos_data in data.get("house", {}).items():
            tracker.house_positions[inst] = Position.from_dict(pos_data)
        for sid, instruments in data.get("agents", {}).items():
            for inst, pos_data in instruments.items():
                tracker.agent_positions[sid][inst] = Position.from_dict(pos_data)
        return tracker
