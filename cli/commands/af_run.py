"""af run — start autonomous trading on Aftermath Finance perpetuals.

Drop-in equivalent of `hl run` but routes all order flow through
Aftermath perpetuals on Sui instead of Hyperliquid.

Instrument names:
  ETH-AF-PERP   — Aftermath ETH perpetual
  BTC-AF-PERP   — Aftermath BTC perpetual
  XAG-AF-PERP   — Aftermath XAG (Silver) perpetual
  SUI-AF-PERP   — Aftermath SUI perpetual

  Short forms are also accepted: ETH, BTC, XAG, SUI (auto-suffixed)
  HL-style names are normalised: ETH-PERP -> ETH-AF-PERP

All Nunchi strategies work unchanged:
  af run avellaneda_mm -i ETH-AF-PERP
  af run engine_mm -i BTC-AF-PERP --tick 10
  af run simple_mm -i XAG-AF-PERP --mock
  af run avellaneda_mm -i ETH --mock --max-ticks 5
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer


def af_run_cmd(
    strategy: str = typer.Argument(
        ...,
        help="Strategy name (e.g., 'avellaneda_mm') or path ('module:ClassName')",
    ),
    instrument: str = typer.Option(
        "ETH-AF-PERP", "--instrument", "-i",
        help="Aftermath instrument (ETH-AF-PERP, BTC-AF-PERP, XAG-AF-PERP, ...)",
    ),
    tick_interval: float = typer.Option(
        10.0, "--tick", "-t",
        help="Seconds between ticks",
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="YAML config file",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Run strategy without placing real orders",
    ),
    max_ticks: int = typer.Option(
        0, "--max-ticks",
        help="Stop after N ticks (0 = run forever)",
    ),
    resume: bool = typer.Option(
        True, "--resume/--fresh",
        help="Resume from saved state or start fresh",
    ),
    data_dir: str = typer.Option(
        "data/af", "--data-dir",
        help="Directory for state and trade logs (default: data/af)",
    ),
    mock: bool = typer.Option(
        False, "--mock",
        help="Use mock market data (no network required)",
    ),
    leverage: int = typer.Option(
        0, "--leverage", "-l",
        help="Override leverage for this run (0 = use AF_LEVERAGE env / default 5)",
    ),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="LLM model override for claude_agent strategy",
    ),
):
    """Start autonomous trading on Aftermath Finance perpetuals.

    Uses the same strategies as `hl run` but routes order flow through
    Aftermath perpetuals on Sui (SUI_PRIVATE_KEY required).

    Example:
      af run avellaneda_mm -i ETH-AF-PERP --tick 15
      af run avellaneda_mm -i ETH --mock --max-ticks 5
    """
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from cli.config import TradingConfig
    from cli.strategy_registry import resolve_strategy_path
    from cli.af_proxy import AftermathProxy, AftermathMockProxy, _normalise_instrument

    if config:
        cfg = TradingConfig.from_yaml(str(config))
    else:
        cfg = TradingConfig()

    cfg.strategy = strategy
    cfg.instrument = _normalise_instrument(instrument)
    cfg.tick_interval = tick_interval
    cfg.dry_run = dry_run
    cfg.max_ticks = max_ticks
    cfg.data_dir = data_dir

    if leverage > 0:
        import os
        os.environ["AF_LEVERAGE"] = str(leverage)

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    strategy_path = resolve_strategy_path(cfg.strategy)

    from sdk.strategy_sdk.loader import load_strategy
    strategy_cls = load_strategy(strategy_path)

    params = dict(cfg.strategy_params)
    if model:
        params["model"] = model

    strategy_instance = strategy_cls(strategy_id=cfg.strategy, **params)

    # Build proxy
    if mock or dry_run:
        hl = AftermathMockProxy()
        mode_label = "DRY RUN" if dry_run else "MOCK"
    else:
        hl = AftermathProxy()
        mode_label = "LIVE (Aftermath)"

    typer.echo(f"Exchange: Aftermath Finance (Sui)")
    typer.echo(f"Mode:     {mode_label}")
    typer.echo(f"Strategy: {cfg.strategy} -> {strategy_path}")
    typer.echo(f"Instrument: {cfg.instrument}")
    typer.echo(f"Tick interval: {cfg.tick_interval}s")
    if cfg.max_ticks > 0:
        typer.echo(f"Max ticks: {cfg.max_ticks}")
    typer.echo("")

    from cli.engine import TradingEngine

    engine = TradingEngine(
        hl=hl,
        strategy=strategy_instance,
        instrument=cfg.instrument,
        tick_interval=cfg.tick_interval,
        dry_run=cfg.dry_run,
        data_dir=cfg.data_dir,
        risk_limits=cfg.to_risk_limits(),
        builder=None,  # No Hyperliquid builder fee on AF
    )

    # Guard integration
    if cfg.guard and cfg.guard.get("enabled"):
        from modules.guard_config import GuardConfig, PRESETS

        preset_name = cfg.guard.get("preset")
        if preset_name and preset_name in PRESETS:
            guard_cfg = GuardConfig.from_dict(PRESETS[preset_name].to_dict())
        else:
            guard_cfg = GuardConfig.from_dict(cfg.guard)

        if "leverage" in cfg.guard:
            guard_cfg.leverage = float(cfg.guard["leverage"])

        engine.guard_config = guard_cfg
        typer.echo(f"Guard: enabled (preset={preset_name or 'custom'})")

    engine.run(max_ticks=cfg.max_ticks, resume=resume)
