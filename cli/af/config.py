"""Configuration for the Aftermath V2 integration.

THE HOST RULE
-------------
``AF_API_BASE_URL`` below is the ONE definition of the Aftermath API host in
this repository. Every call site reads it; nothing else hardcodes a hostname.

This is a correctness requirement, not a style preference. A repository with
the host smeared across forty runtime files is a repository that breaks on the
next migration. ``tests/test_af_v2_hosts.py`` pins the launched production host
and rejects the retired preview hostname.

The production V2 API is served from the main Aftermath domain. The former
preview deployment still answers but has a stale market universe, so it must
not be used as a fallback. See ``AFTERMATH_SKILLS_REF/README-DELTA.md``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# ── THE one host constant ────────────────────────────────────────
#: Aftermath V2 API base URL. Override with ``AF_API_BASE_URL`` in the
#: environment; never hardcode a host anywhere else.
AF_API_BASE_URL_DEFAULT = "https://aftermath.finance"


def AF_API_BASE_URL() -> str:
    """Resolve the API base URL. The single source of truth for the host."""
    return os.getenv("AF_API_BASE_URL", AF_API_BASE_URL_DEFAULT).rstrip("/")


# ── Defaults ─────────────────────────────────────────────────────

#: USDC on Sui mainnet -- the collateral coin type for perpetuals accounts.
#: Baked in so a turnkey user never pastes a coin type by hand.
AF_COLLATERAL_COIN_TYPE_DEFAULT = (
    "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7"
    "::usdc::USDC"
)

#: Sui's native gas coin.
SUI_COIN_TYPE = "0x2::sui::SUI"

#: Gas budget in MIST. Always set explicitly -- auto-estimation under-counts the
#: storage cost of created objects and surfaces later as InsufficientGas on a
#: transaction that simulated cleanly.
AF_GAS_BUDGET_MIST_DEFAULT = 50_000_000  # 0.05 SUI

AF_LEVERAGE_DEFAULT = 5.0

#: Milliseconds to wait after a mutation before re-reading state. Sui settles
#: asynchronously; reading immediately returns the pre-transaction view.
AF_SETTLE_MS_DEFAULT = 1500


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AfConfig:
    """Resolved Aftermath configuration for one process.

    Turnkey contract: the only value a user MUST supply is the wallet secret
    (``AF_WALLET_KEY``). Everything below has a defensible default, and the
    account, market ids and collateral type are discovered from the API rather
    than pasted by hand.
    """

    base_url: str = field(default_factory=AF_API_BASE_URL)
    collateral_coin_type: str = AF_COLLATERAL_COIN_TYPE_DEFAULT

    # Gas -- see cli/af/gas.py. One value selects the mode.
    gas_mode: str = "sponsored"
    gas_budget_mist: int = AF_GAS_BUDGET_MIST_DEFAULT
    #: Which coin pays gas in `dynamic` mode. Ignored in other modes.
    gas_coin_type: str = AF_COLLATERAL_COIN_TYPE_DEFAULT

    # Identity. The wallet SECRET is never stored on this object -- only the
    # address, which is public. See `wallet_key_present`.
    wallet_address: Optional[str] = None
    wallet_key_present: bool = False

    # Trading
    leverage: float = AF_LEVERAGE_DEFAULT
    settle_ms: int = AF_SETTLE_MS_DEFAULT

    #: Master arming switch. Nothing is signed or submitted unless this is true
    #: AND a signer is wired up. Turnkey means "no wiring", not "trades on the
    #: first run".
    armed: bool = False

    # Integrator / builder code (v3.0.0: integratorId is a u32 NUMBER, and the
    # fee field is `integratorFee`, not `takerFee`).
    integrator_id: Optional[int] = None
    integrator_fee: Optional[float] = None

    # Safety
    heartbeat_timeout_s: float = 90.0
    max_retries: int = 4

    def builder_code(self) -> Optional[dict]:
        """The v3.0.0 ``builderCode`` object, or None when not configured."""
        if self.integrator_id is None or self.integrator_fee is None:
            return None
        return {
            "integratorId": int(self.integrator_id),
            "integratorFee": float(self.integrator_fee),
        }


def load_config() -> AfConfig:
    """Build an :class:`AfConfig` from the environment.

    Reads the presence of the wallet secret but never its value -- the secret is
    only ever handed to a signer, and this process deliberately ships without
    one wired up.
    """
    from cli.af.gas import parse_gas_mode  # local import: avoids a cycle

    key_present = bool(
        os.getenv("AF_WALLET_KEY") or os.getenv("SUI_PRIVATE_KEY")
    )

    integrator_id = os.getenv("AF_INTEGRATOR_ID")
    integrator_fee = os.getenv("AF_INTEGRATOR_FEE")

    return AfConfig(
        base_url=AF_API_BASE_URL(),
        collateral_coin_type=os.getenv(
            "AF_COLLATERAL_COIN_TYPE", AF_COLLATERAL_COIN_TYPE_DEFAULT
        ),
        gas_mode=parse_gas_mode(os.getenv("AF_GAS_MODE")),
        gas_budget_mist=_env_int("AF_GAS_BUDGET_MIST", AF_GAS_BUDGET_MIST_DEFAULT),
        gas_coin_type=os.getenv("AF_GAS_COIN_TYPE", AF_COLLATERAL_COIN_TYPE_DEFAULT),
        wallet_address=os.getenv("AF_WALLET_ADDRESS") or None,
        wallet_key_present=key_present,
        leverage=_env_float("AF_LEVERAGE", AF_LEVERAGE_DEFAULT),
        settle_ms=_env_int("AF_SETTLE_MS", AF_SETTLE_MS_DEFAULT),
        armed=_env_bool("AF_ARMED", False),
        integrator_id=int(integrator_id) if integrator_id else None,
        integrator_fee=float(integrator_fee) if integrator_fee else None,
        heartbeat_timeout_s=_env_float("AF_HEARTBEAT_TIMEOUT_S", 90.0),
        max_retries=_env_int("AF_MAX_RETRIES", 4),
    )
