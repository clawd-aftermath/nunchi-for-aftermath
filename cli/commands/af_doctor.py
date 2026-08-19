"""``nunchi doctor`` — Aftermath V2 preflight.

The first thing a new user runs. It answers one question: if I start a bot
right now, will it work? Every check states what it found and, when it fails,
what to do about it. The command exits non-zero on any failure so it can gate a
deployment.

Nothing here signs, submits or broadcasts, and no private key is read.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

import typer


PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    remedy: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _render(checks: List[Check]) -> None:
    width = max((len(c.name) for c in checks), default=10)
    colour = {PASS: typer.colors.GREEN, WARN: typer.colors.YELLOW, FAIL: typer.colors.RED}
    typer.echo("")
    typer.echo(f"  {'CHECK'.ljust(width)}  STATUS  DETAIL")
    typer.echo(f"  {'-' * width}  ------  {'-' * 46}")
    for c in checks:
        badge = typer.style(c.status.ljust(6), fg=colour.get(c.status, typer.colors.WHITE), bold=True)
        typer.echo(f"  {c.name.ljust(width)}  {badge}  {c.detail}")
        if c.remedy and c.status != PASS:
            typer.echo(f"  {' ' * width}          -> {c.remedy}")
    typer.echo("")


def af_doctor_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show raw API responses"),
) -> None:
    """Validate the Aftermath V2 setup and report a pass/fail table."""
    from cli.af import api, gas as gasmod
    from cli.af.config import AF_API_BASE_URL, AF_API_BASE_URL_DEFAULT, load_config
    from cli.af.markets import MarketRegistry

    checks: List[Check] = []

    # 1. Config loads at all.
    try:
        cfg = load_config()
    except ValueError as exc:
        _render([Check("config", FAIL, str(exc), "Fix the offending environment variable.")])
        raise typer.Exit(1)

    # 2. Host.
    host = AF_API_BASE_URL()
    if host == AF_API_BASE_URL_DEFAULT:
        checks.append(Check("api host", PASS, host))
    else:
        checks.append(
            Check(
                "api host",
                WARN,
                f"{host} (non-default)",
                f"The live V2 host is {AF_API_BASE_URL_DEFAULT}. The legacy v1 "
                "host is retired and no longer serves the API at all.",
            )
        )

    # 3. API reachable + spec compatible.
    spec_paths = 0
    try:
        spec = api.get("/api/openapi/spec.json", max_retries=1)
        spec_paths = len(spec.get("paths", {})) if isinstance(spec, dict) else 0
        if spec_paths >= 200:
            checks.append(Check("api reachable", PASS, f"spec served, {spec_paths} paths"))
        else:
            checks.append(
                Check(
                    "api reachable",
                    WARN,
                    f"spec served but only {spec_paths} paths (V2 has 251)",
                    "This may be the retired v1 API (159 paths). Check AF_API_BASE_URL.",
                )
            )
    except api.AfApiError as exc:
        checks.append(
            Check("api reachable", FAIL, str(exc)[:70], "Check network access and AF_API_BASE_URL.")
        )

    # 4. Wallet identity. The SECRET is never read — only its presence.
    if cfg.wallet_address:
        checks.append(Check("wallet address", PASS, f"{cfg.wallet_address[:10]}…{cfg.wallet_address[-4:]}"))
    else:
        checks.append(
            Check(
                "wallet address",
                FAIL,
                "AF_WALLET_ADDRESS not set",
                "Set AF_WALLET_ADDRESS to your Sui address (public — not the key).",
            )
        )
    checks.append(
        Check(
            "wallet key",
            PASS if cfg.wallet_key_present else WARN,
            "AF_WALLET_KEY present (not read)" if cfg.wallet_key_present else "AF_WALLET_KEY not set",
            None if cfg.wallet_key_present else "Required only to sign; read paths work without it.",
        )
    )

    # 5. Gas mode + its prerequisites.
    gas_check = gasmod.check_gas_mode(
        cfg.gas_mode,
        wallet_address=cfg.wallet_address,
        sui_balance_mist=None,
        gas_coin_type=cfg.gas_coin_type,
        budget_mist=cfg.gas_budget_mist,
    )
    checks.append(
        Check(
            f"gas ({cfg.gas_mode})",
            PASS if gas_check.ok else WARN,
            gas_check.detail,
            gas_check.remedy,
        )
    )
    checks.append(
        Check("gas budget", PASS, f"{cfg.gas_budget_mist} MIST ({gasmod.fmt_sui(cfg.gas_budget_mist)}), explicit")
    )

    # 6. Markets resolve. An empty response leaves trading unavailable.
    registry = MarketRegistry(cfg.collateral_coin_type)
    markets = []
    try:
        markets = registry.refresh(force=True)
        if markets:
            symbols = ", ".join(sorted(m.symbol for m in markets)[:8])
            checks.append(Check("markets", PASS, f"{len(markets)} listed: {symbols}"))
        else:
            checks.append(
                Check(
                    "markets",
                    WARN,
                    "no markets listed for this collateral type",
                    "Expected before the relaunch. Trading paths stay idle until markets exist.",
                )
            )
    except api.AfApiError as exc:
        checks.append(Check("markets", FAIL, str(exc)[:70], "Check AF_COLLATERAL_COIN_TYPE and the API host."))

    # 7. Account discovery.
    if cfg.wallet_address:
        try:
            res = api.post(
                "/api/perpetuals/accounts/owned",
                {"walletAddress": cfg.wallet_address, "collateralCoinTypes": [cfg.collateral_coin_type]},
            )
            caps = (res or {}).get("accountCaps") or []
            if caps:
                acc_id = caps[0].get("accountId")
                checks.append(Check("account", PASS, f"{len(caps)} account(s), primary id {acc_id}"))
            else:
                checks.append(
                    Check(
                        "account",
                        WARN,
                        "wallet owns no perpetuals account",
                        "Create one via /api/perpetuals/transactions/create-account. "
                        "Onboarding can be a single atomic PTB (create + deposit + allocate).",
                    )
                )
        except api.AfApiError as exc:
            checks.append(Check("account", FAIL, str(exc)[:70], "Check the wallet address and API host."))
    else:
        checks.append(Check("account", FAIL, "skipped — no wallet address", "Set AF_WALLET_ADDRESS."))

    # 8. Arming posture.
    checks.append(
        Check(
            "armed",
            WARN if cfg.armed else PASS,
            "ARMED — live orders enabled" if cfg.armed else "disarmed (no orders will be submitted)",
            "Unset AF_ARMED unless you intend to trade real funds." if cfg.armed else None,
        )
    )

    # 9. Collateral coin type.
    checks.append(Check("collateral", PASS, gasmod.short_coin(cfg.collateral_coin_type)))

    _render(checks)

    failures = [c for c in checks if c.failed]
    warnings = [c for c in checks if c.status == WARN]
    if failures:
        typer.secho(f"{len(failures)} check(s) FAILED.", fg=typer.colors.RED, bold=True)
        raise typer.Exit(1)
    if warnings:
        typer.secho(f"All required checks passed ({len(warnings)} warning(s)).", fg=typer.colors.YELLOW)
    else:
        typer.secho("All checks passed.", fg=typer.colors.GREEN, bold=True)

    if verbose:
        typer.echo(f"\nbase_url={AF_API_BASE_URL()}\nspec_paths={spec_paths}\nmarkets={len(markets)}")
