"""Transaction pipeline: build -> preview-gate -> INSPECT -> (sign) -> reconcile.

A builder response is untrusted input. It arrives as opaque base64 and signing
it is irreversible, so between "the server handed me a transaction" and "I
signed it" there must be a step that proves the transaction is the one we asked
for.

The gate is enforced structurally, not by convention. :func:`sign_inspected`
accepts only an :class:`InspectedTx`, and the only way to obtain one is
:func:`inspect`, whose constructor guard rejects instances built any other way.
There is no path from a raw builder response to a signature that skips
inspection.

What V2 actually returns
------------------------
Native ``/transactions/*`` routes return ``{"txKind", "sponsorSignature"}`` --
NOT ``{"transactionBytes", "signingDigest"}``. The spec spells out the two
cases:

* **Unsponsored** (``sponsorSignature`` absent/null): ``txKind`` is a base64
  ``TransactionKind`` the client completes, signs and submits.
* **Sponsored** (``sponsorSignature`` present): ``txKind`` carries base64 BCS
  ``Transaction`` bytes with gas payment already attached; the client signs
  those bytes and submits them together with the sponsor's signature.

That asymmetry is the thing inspection checks hardest, because a mismatch
between the declared gas mode and what the builder actually returned means gas
is being paid by someone other than who the operator chose.

SAFETY: nothing in this module signs, submits or broadcasts. ``SignFn`` is
supplied by the caller and this repository deliberately ships without one
wired up.
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from cli.af import api
from cli.af.gas import GasConfig, apply_gas_to_body

log = logging.getLogger("af.tx")


class TxInspectionError(RuntimeError):
    """A built transaction did not match the caller's intent. Never ignorable."""

    def __init__(self, message: str, intent: str = ""):
        super().__init__(f"transaction inspection failed ({intent}): {message}" if intent else message)
        self.intent = intent


class NotArmedError(RuntimeError):
    """Something tried to sign or submit while the bot is disarmed.

    Arming is a single explicit flag (``AF_ARMED``). Turnkey means "no wiring
    required", not "trades on the first run".
    """


@dataclass(frozen=True)
class TxExpectation:
    """What the caller believes it asked the builder for."""

    sender: str
    gas: GasConfig
    #: Human-readable description, used in error messages.
    intent: str
    #: Package id the transaction must target, when known.
    expected_package: Optional[str] = None


# Sentinel that only this module can produce. An InspectedTx constructed
# without it raises, so possession of one is proof the gate ran.
_GATE = object()


@dataclass(frozen=True)
class InspectedTx:
    """A transaction that has passed inspection."""

    tx_kind: str
    sponsor_signature: Optional[str]
    expectation: TxExpectation
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    _token: Any = None

    def __post_init__(self) -> None:
        if self._token is not _GATE:
            raise TxInspectionError(
                "InspectedTx may only be created by cli.af.tx.inspect() — "
                "the inspection gate cannot be bypassed"
            )

    @property
    def is_sponsored(self) -> bool:
        return bool(self.sponsor_signature)


def _is_base64(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    try:
        base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _pick_str(obj: Dict[str, Any], keys: Sequence[str]) -> Optional[str]:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def inspect(built: Any, expectation: TxExpectation) -> InspectedTx:
    """Verify a built transaction matches intent, or raise.

    Raises rather than returning a falsy value on purpose: an inspection
    failure must not be silently ignorable by a caller that forgets to check a
    boolean.
    """
    intent = expectation.intent

    if not isinstance(built, dict):
        raise TxInspectionError("builder returned no transaction object", intent)

    tx_kind = built.get("txKind")
    if not isinstance(tx_kind, str) or not tx_kind:
        raise TxInspectionError(
            "response carries no txKind — refusing to sign an unidentified payload", intent
        )
    if not _is_base64(tx_kind):
        raise TxInspectionError("txKind is not valid base64", intent)

    sponsor_sig = built.get("sponsorSignature")
    if sponsor_sig is not None and not isinstance(sponsor_sig, str):
        raise TxInspectionError(f"sponsorSignature has unexpected type {type(sponsor_sig).__name__}", intent)

    # Gas-mode agreement. Sponsor and sender MAY be the same address on Sui, so
    # nothing here asserts they differ -- only that sponsorship is present when
    # the operator asked for it and absent when they did not.
    mode = expectation.gas.mode
    if mode == "self" and sponsor_sig:
        raise TxInspectionError(
            "gas mode is 'self' but the builder returned a sponsor signature — "
            "someone else would pay for this transaction",
            intent,
        )
    if mode == "sponsored" and expectation.gas.sponsor and not sponsor_sig:
        raise TxInspectionError(
            "gas mode is 'sponsored' with a resolved sponsor, but the builder "
            "returned no sponsor signature — gas would fall back to the sender",
            intent,
        )

    # Sender echo, where the builder provides one.
    echoed_sender = _pick_str(built, ("sender", "walletAddress", "from"))
    if echoed_sender and echoed_sender.lower() != str(expectation.sender).lower():
        raise TxInspectionError(
            f"sender mismatch: expected {expectation.sender}, transaction is for {echoed_sender}",
            intent,
        )

    # Package target, when known and echoed.
    echoed_pkg = _pick_str(built, ("packageId", "package", "target"))
    if expectation.expected_package and echoed_pkg and echoed_pkg != expectation.expected_package:
        raise TxInspectionError(
            f"package mismatch: expected {expectation.expected_package}, transaction targets {echoed_pkg}",
            intent,
        )

    log.debug("inspected ok: %s (sponsored=%s)", intent, bool(sponsor_sig))
    return InspectedTx(
        tx_kind=tx_kind,
        sponsor_signature=sponsor_sig,
        expectation=expectation,
        raw=built,
        _token=_GATE,
    )


# ── Signing (caller-supplied; nothing here signs) ────────────────

#: Signs a digest. Supplied by the caller; this repository ships without one.
SignFn = Callable[[str], str]


def sign_inspected(tx: InspectedTx, signers: Sequence[SignFn], *, armed: bool) -> List[str]:
    """Produce signatures for an INSPECTED transaction.

    ``/api/ccxt/submit/*`` accepts ``signatures[]`` -- plural. When the sender
    and the gas owner differ, both sign the SAME digest, hence a list always.

    Requires ``armed=True``; a disarmed process raises rather than signing.
    """
    if not armed:
        raise NotArmedError(
            "refusing to sign: the bot is disarmed. Set AF_ARMED=true only when "
            "you intend to trade with real funds."
        )
    if not isinstance(tx, InspectedTx):
        raise TxInspectionError("sign_inspected requires an InspectedTx from inspect()")
    if not signers:
        raise ValueError("no signers supplied")
    return [sign(tx.tx_kind) for sign in signers]


# ── Preview gating ───────────────────────────────────────────────


def build_gated(
    *,
    build_path: str,
    body: Dict[str, Any],
    gas: GasConfig,
    expectation: TxExpectation,
    preview_path: Optional[str] = None,
) -> InspectedTx:
    """Build a transaction behind a preview gate, then inspect it.

    When a preview counterpart exists it runs FIRST, and a preview error blocks
    the build entirely -- no transaction is constructed for an operation
    already known to fail. The gate is not optional and not bypassable by a
    flag: omitting ``preview_path`` is only correct for routes that genuinely
    have no preview counterpart in the spec.
    """
    full_body = apply_gas_to_body(body, gas)

    if preview_path:
        result = api.preview(preview_path, full_body)
        if not result.ok:
            raise TxInspectionError(f"preview rejected: {result.error}", expectation.intent)

    built = api.post(build_path, full_body)
    return inspect(built, expectation)


# ── Reconciliation ───────────────────────────────────────────────


def reconcile(
    verify: Callable[[], bool],
    *,
    attempts: int = 5,
    delay_s: float = 0.4,
    intent: str = "",
) -> None:
    """Confirm intent actually took effect by re-reading authoritative state.

    A 200 from submit means "accepted", not "applied". Sui settles
    asynchronously, so ``verify`` must re-read from the API and return True
    only when the expected end state is actually observed.
    """
    for i in range(attempts):
        try:
            if verify():
                return
        except api.AfApiError as exc:
            log.debug("reconcile attempt %d failed to read state: %s", i + 1, exc)
        if i < attempts - 1:
            time.sleep(delay_s * (i + 1))
    raise RuntimeError(
        f"reconciliation failed{f' ({intent})' if intent else ''}: "
        f"expected state not observed after {attempts} attempts"
    )
