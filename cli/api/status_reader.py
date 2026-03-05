"""Read agent status from StateDB and WOLF state files.

Shared utility used by:
- scripts/entrypoint.py (imported directly)
- deploy/openclaw-railway/src/server.js (via `python3 -m cli.api.status_reader`)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


def read_status(data_dir: str = "data") -> Dict[str, Any]:
    """Read unified agent status from StateDB + WOLF state.json.

    Checks both `{data_dir}/cli/state.db` (TradingEngine)
    and `{data_dir}/wolf/state.json` (WOLF orchestrator).
    """
    result: Dict[str, Any] = {"status": "stopped"}

    # Try WOLF state first (higher priority — WOLF wraps strategies)
    wolf_state = _read_wolf_state(f"{data_dir}/wolf")
    if wolf_state:
        result.update(wolf_state)
        result["status"] = "running"
        return result

    # Fall back to single-strategy StateDB
    engine_state = _read_engine_state(f"{data_dir}/cli")
    if engine_state:
        result.update(engine_state)
        result["status"] = "running"
        return result

    return result


def _read_wolf_state(wolf_dir: str) -> Dict[str, Any] | None:
    """Read WOLF orchestrator state from state.json."""
    state_path = Path(wolf_dir) / "state.json"
    if not state_path.exists():
        return None

    try:
        with open(state_path) as f:
            state = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None

    active_slots = [s for s in state.get("slots", []) if s.get("status") == "active"]
    closed_slots = [s for s in state.get("slots", []) if s.get("status") == "closed"]

    return {
        "engine": "wolf",
        "tick_count": state.get("tick_count", 0),
        "daily_pnl": state.get("daily_pnl", 0.0),
        "total_pnl": state.get("total_pnl", 0.0),
        "total_trades": state.get("total_trades", 0),
        "max_slots": state.get("max_slots", 3),
        "active_slots": active_slots,
        "closed_slots": closed_slots[-5:],  # last 5 closed
        "positions": [
            {
                "slot": s.get("slot_id"),
                "market": s.get("instrument", ""),
                "side": s.get("side", ""),
                "size": s.get("entry_size", 0),
                "entry": s.get("entry_price", 0),
                "roe": s.get("roe_pct", 0),
                "phase": s.get("dsl_phase", 0),
            }
            for s in active_slots
        ],
    }


def _read_engine_state(cli_dir: str) -> Dict[str, Any] | None:
    """Read single-strategy state from StateDB."""
    db_path = Path(cli_dir) / "state.db"
    if not db_path.exists():
        return None

    # Import here to avoid top-level dependency issues when run standalone
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from parent.store import StateDB

        db = StateDB(path=str(db_path))
        try:
            tick_count = db.get("tick_count") or 0
            strategy_id = db.get("strategy_id") or ""
            instrument = db.get("instrument") or ""
            order_stats = db.get("order_stats") or {}
            positions_data = db.get("positions")

            pos_qty = 0.0
            upnl = 0.0
            rpnl = 0.0
            if positions_data:
                for _agent_id, instruments in positions_data.get("agents", {}).items():
                    for _inst, pos in instruments.items():
                        pos_qty = float(pos.get("net_qty", "0"))
                        upnl = float(pos.get("unrealized_pnl", "0"))
                        rpnl = float(pos.get("realized_pnl", "0"))

            return {
                "engine": strategy_id,
                "tick_count": tick_count,
                "instrument": instrument,
                "position_qty": pos_qty,
                "unrealized_pnl": upnl,
                "realized_pnl": rpnl,
                "total_orders": order_stats.get("total_placed", 0),
                "total_fills": order_stats.get("total_filled", 0),
            }
        finally:
            db.close()
    except Exception:
        return None


def read_strategies() -> Dict[str, Any]:
    """Return strategy catalog from strategy_registry."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.strategy_registry import STRATEGY_REGISTRY, YEX_MARKETS

    strategies = {}
    for name, info in STRATEGY_REGISTRY.items():
        strategies[name] = {
            "description": info["description"],
            "params": info["params"],
        }

    return {
        "strategies": strategies,
        "markets": {
            name: info["description"]
            for name, info in YEX_MARKETS.items()
        },
    }


# CLI entry point: python3 -m cli.api.status_reader [status|strategies] [--data-dir DIR]
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "strategies"], default="status", nargs="?")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    if args.command == "strategies":
        print(json.dumps(read_strategies(), indent=2))
    else:
        print(json.dumps(read_status(args.data_dir), indent=2))
