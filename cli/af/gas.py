"""Gas payment modes -- the user's choice, not the bot's.

Three modes, selected by one config value (``AF_GAS_MODE``):

``sponsored``
    A gas pool pays. The user needs no SUI at all. This is the default so a
    freshly funded USDC wallet can trade immediately, which is the whole point
    of the turnkey bar.

``self``
    The user's own SUI pays, the ordinary Sui gas path.

``dynamic``
    USDC-as-gas (or any supported coin) via ``/api/dynamic-gas``. The user also
    chooses *which* coin pays, through ``AF_GAS_COIN_TYPE``.

Two API facts worth stating plainly, because both are easy to get backwards:

* ``/api/gas-pool/pool`` is **POST** and requires ``{"walletAddress": …}``.
* ``/api/dynamic-gas`` is **POST** and requires
  ``{"serializedTx", "walletAddress", "gasCoinType"}``. It is a *transform*
  endpoint that rewrites a built transaction to pay gas in another coin -- it
  is not a health check, and there is no meaningful way to ping it without a
  real transaction in hand.

Sponsor and sender MAY legitimately be the same address on Sui. Nothing here
asserts they differ.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cli.af import api
from cli.af.config import AF_GAS_BUDGET_MIST_DEFAULT, SUI_COIN_TYPE

log = logging.getLogger("af.gas")

GAS_MODES = ("sponsored", "self", "dynamic")


def is_gas_mode(value: object) -> bool:
    return isinstance(value, str) and value in GAS_MODES


def parse_gas_mode(value: Optional[str], fallback: str = "sponsored") -> str:
    """Parse ``AF_GAS_MODE``. An unknown value is an error, never a silent default.

    Silently falling back would mean a user who typed ``AF_GAS_MODE=sponsered``
    gets a different gas source than they asked for and only discovers it when
    a transaction fails or an unexpected wallet pays.
    """
    if value is None or value.strip() == "":
        return fallback
    v = value.strip().lower()
    if not is_gas_mode(v):
        raise ValueError(
            f"invalid AF_GAS_MODE {value!r}. Expected one of: {', '.join(GAS_MODES)}"
        )
    return v


@dataclass(frozen=True)
class GasConfig:
    """The gas decision for one process."""

    mode: str = "sponsored"
    #: Explicit budget in MIST. Never rely on auto-estimation.
    budget_mist: int = AF_GAS_BUDGET_MIST_DEFAULT
    #: Resolved sponsor address, when the sponsored path finds one.
    sponsor: Optional[str] = None
    #: Coin that pays gas in `dynamic` mode.
    gas_coin_type: str = SUI_COIN_TYPE

    def __post_init__(self) -> None:
        if not is_gas_mode(self.mode):
            raise ValueError(f"invalid gas mode {self.mode!r}")
        if self.budget_mist <= 0:
            raise ValueError("gas budget must be positive and explicit")


def apply_gas_to_body(body: Dict[str, Any], gas: GasConfig) -> Dict[str, Any]:
    """Return a NEW request body carrying this process's gas decision.

    The spec models sponsorship as ``sponsor: {"walletAddress": …}`` -- an
    object, not a bare address string. Sending a string is accepted by nothing
    and rejected confusingly, so it is constructed here once.
    """
    out = dict(body)
    out["gasBudget"] = str(gas.budget_mist)

    if gas.mode == "sponsored" and gas.sponsor:
        out["sponsor"] = {"walletAddress": gas.sponsor}
        out["isSponsoredTx"] = True
    elif gas.mode == "self":
        out.pop("sponsor", None)
        out["isSponsoredTx"] = False
    # `dynamic` builds unsponsored, then rewrites the built bytes through
    # /api/dynamic-gas -- see `apply_dynamic_gas`.
    return out


def resolve_sponsor(wallet_address: str) -> Optional[str]:
    """Look up the gas pool for a wallet and return the sponsoring address.

    Returns None rather than raising when no pool is reachable: whether that is
    fatal depends on the active gas mode, and only the caller knows.
    """
    try:
        pool = api.post("/api/gas-pool/pool", {"walletAddress": wallet_address})
    except api.AfApiError as exc:
        log.debug("gas pool lookup failed: %s", exc)
        return None
    if not isinstance(pool, dict):
        return None
    # The pool response is keyed on the OWNER's wallet address; the sponsoring
    # object is `gasPoolId`. Sponsor and sender may be the same address, which
    # is exactly the self-sponsored case.
    for key in ("sponsorAddress", "sponsor", "walletAddress"):
        val = pool.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def gas_pool_status(wallet_address: str) -> Dict[str, Any]:
    """Raw gas-pool record for a wallet. Raises on transport failure."""
    res = api.post("/api/gas-pool/pool", {"walletAddress": wallet_address})
    return res if isinstance(res, dict) else {}


def apply_dynamic_gas(serialized_tx: str, wallet_address: str, gas_coin_type: str) -> Dict[str, Any]:
    """Rewrite a built transaction to pay gas in ``gas_coin_type``.

    Returns ``{"txBytes", "sponsoredSignature"}``. This is the only mode where
    the transaction bytes change *after* the builder produced them, which is
    why inspection in :mod:`cli.af.tx` runs against the post-transform bytes.
    """
    res = api.post(
        "/api/dynamic-gas",
        {
            "serializedTx": serialized_tx,
            "walletAddress": wallet_address,
            "gasCoinType": gas_coin_type,
        },
    )
    if not isinstance(res, dict) or "txBytes" not in res:
        raise api.AfApiError(f"/api/dynamic-gas returned an unexpected body: {res!r}")
    return res


# ── Preflight (used by `doctor`) ─────────────────────────────────


@dataclass(frozen=True)
class GasCheck:
    mode: str
    ok: bool
    detail: str
    remedy: Optional[str] = None


def check_gas_mode(
    mode: str,
    *,
    wallet_address: Optional[str] = None,
    sui_balance_mist: Optional[int] = None,
    gas_coin_type: Optional[str] = None,
    budget_mist: int = AF_GAS_BUDGET_MIST_DEFAULT,
) -> GasCheck:
    """Verify the active mode's prerequisites actually hold.

    Each mode needs something different, and discovering the gap at trade time
    is precisely the failure this exists to prevent.
    """
    if mode == "sponsored":
        if not wallet_address:
            return GasCheck(mode, False, "wallet address not configured (needed to find the gas pool)",
                            "Set AF_WALLET_ADDRESS, or let `doctor` derive it from AF_WALLET_KEY.")
        try:
            pool = gas_pool_status(wallet_address)
        except api.AfApiError as exc:
            return GasCheck(mode, False, f"gas pool unreachable: {exc}",
                            "Set AF_GAS_MODE=self and fund the wallet with SUI, or retry when the pool is up.")
        balance = pool.get("balance")
        pool_id = pool.get("gasPoolId")
        if not pool_id:
            return GasCheck(mode, False, "no gas pool exists for this wallet",
                            "Create/fund a gas pool (/api/gas-pool/transactions/create), "
                            "or set AF_GAS_MODE=self.")
        return GasCheck(mode, True, f"gas pool {str(pool_id)[:10]}… reachable, balance {balance} MIST — no SUI required")

    if mode == "self":
        if sui_balance_mist is None:
            return GasCheck(mode, False, "SUI balance unknown (wallet address not configured)",
                            "Set AF_WALLET_ADDRESS, or switch to AF_GAS_MODE=sponsored.")
        if sui_balance_mist <= 0:
            return GasCheck(mode, False, "wallet holds 0 SUI but 'self' mode pays gas from it",
                            "Fund the wallet with SUI, or set AF_GAS_MODE=sponsored to trade without holding SUI.")
        if sui_balance_mist < budget_mist:
            return GasCheck(mode, False,
                            f"SUI balance {fmt_sui(sui_balance_mist)} is below the gas budget {fmt_sui(budget_mist)}",
                            "Add SUI, lower AF_GAS_BUDGET_MIST, or set AF_GAS_MODE=sponsored.")
        return GasCheck(mode, True, f"wallet holds {fmt_sui(sui_balance_mist)}")

    if mode == "dynamic":
        if not wallet_address:
            return GasCheck(mode, False, "wallet address not configured", "Set AF_WALLET_ADDRESS.")
        if not gas_coin_type:
            return GasCheck(mode, False, "AF_GAS_COIN_TYPE not set — dynamic gas needs the coin that pays",
                            "Set AF_GAS_COIN_TYPE (e.g. the USDC coin type), or use AF_GAS_MODE=sponsored.")
        # /api/dynamic-gas is a transform, not a health check: it requires a
        # real serialized transaction. Validate preconditions and confirm the
        # path on first use rather than fabricating a probe transaction.
        return GasCheck(mode, True,
                        f"preconditions satisfied; gas paid in {short_coin(gas_coin_type)} "
                        "(endpoint exercised on the first transaction)")

    raise ValueError(f"unknown gas mode {mode!r}")


def short_coin(coin_type: str) -> str:
    """Last segment of a coin type, for readable output."""
    return coin_type.split("::")[-1] if "::" in coin_type else coin_type


def fmt_sui(mist: int) -> str:
    whole, frac = divmod(int(mist), 1_000_000_000)
    frac_s = str(frac).rjust(9, "0").rstrip("0")
    return f"{whole}.{frac_s} SUI" if frac_s else f"{whole} SUI"
