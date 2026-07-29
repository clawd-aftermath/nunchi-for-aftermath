"""Identifier types and the native BigInt wire format.

Mixing up account identifiers is the single most common way an integration
against this API fails (skills v3.0.0, ``gotchas.md`` §1). Three different
things are all loosely called "the account":

===========================  ==========  ===================================
identity                     runtime     wire form
===========================  ==========  ===================================
native ``accountId``         int         BigInt string, ``"123n"``
CCXT *write* ``accountId``   str         account-capability object id, ``0x…``
CCXT read/stream             int         plain JSON number
  ``accountNumber``
===========================  ==========  ===================================

Passing one where another belongs is silently accepted by a bare ``str`` or
``int`` parameter and then fails at the API -- or worse, addresses a different
account. Python has no compile-time brands, so each identity is a distinct
class here: passing the wrong one is a ``TypeError`` raised at the boundary
rather than an unexplained rejection several calls later.

Each constructor validates before wrapping, so holding one of these objects is
also proof the value was checked.
"""
from __future__ import annotations

import re
from typing import Any, Union

_OBJECT_ID_RE = re.compile(r"^0x[0-9a-fA-F]{1,64}$")
_NATIVE_BIGINT_RE = re.compile(r"^(-?\d+)n$")
_DIGITS_MAYBE_N_RE = re.compile(r"^(\d+)n?$")


class _Id:
    """Base for the identifier wrappers. Immutable, hashable, printable."""

    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        object.__setattr__(self, "_value", value)

    @property
    def value(self):  # noqa: ANN201 - concrete type varies by subclass
        return self._value

    def __setattr__(self, *_args: Any) -> None:  # pragma: no cover - guard
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self._value == other._value  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((type(self).__name__, self._value))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._value!r})"


class NativeAccountId(_Id):
    """Native perpetuals account id.

    Numeric identity. Transported as the BigInt string ``"123n"`` -- a plain
    JSON number is rejected by the API.
    """

    __slots__ = ()

    def __init__(self, value: Union[int, str, "NativeAccountId"]) -> None:
        if isinstance(value, NativeAccountId):
            value = value.value
        if isinstance(value, bool):  # bool is an int subclass; reject it
            raise TypeError("native accountId must be an integer, got a bool")
        if isinstance(value, int):
            v = value
        elif isinstance(value, str):
            m = _DIGITS_MAYBE_N_RE.match(value.strip())
            if not m:
                raise TypeError(
                    "native accountId must be digits, optionally 'n'-suffixed; "
                    f"got {value!r}"
                )
            v = int(m.group(1))
        else:
            raise TypeError(f"native accountId must be int or str, got {type(value).__name__}")
        if v < 0:
            raise TypeError(f"native accountId must be non-negative, got {v}")
        super().__init__(v)

    def wire(self) -> str:
        """The mandatory ``"123n"`` request form."""
        return f"{self._value}n"

    def __int__(self) -> int:
        return self._value


class AccountCapId(_Id):
    """CCXT *write* account id -- an account-capability OBJECT id (``0x…``)."""

    __slots__ = ()

    def __init__(self, value: Union[str, "AccountCapId"]) -> None:
        if isinstance(value, AccountCapId):
            value = value.value
        if not isinstance(value, str) or not _OBJECT_ID_RE.match(value.strip()):
            raise TypeError(
                f"accountCapId must be an object id ('0x…'), got {value!r}. "
                "If you meant the numeric native account id, use NativeAccountId."
            )
        super().__init__(value.strip())

    def __str__(self) -> str:
        return self._value


class AccountNumber(_Id):
    """CCXT read/stream account number -- a plain JSON number."""

    __slots__ = ()

    def __init__(self, value: Union[int, "AccountNumber"]) -> None:
        if isinstance(value, AccountNumber):
            value = value.value
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError(f"accountNumber must be a non-negative integer, got {value!r}")
        super().__init__(value)

    def __int__(self) -> int:
        return self._value


class MarketId(_Id):
    """A market identifier.

    NOT a ticker. Aftermath validates these strictly; they are resolved from
    ``/api/perpetuals/all-markets`` and never constructed from a symbol.
    """

    __slots__ = ()

    def __init__(self, value: Union[str, "MarketId"]) -> None:
        if isinstance(value, MarketId):
            value = value.value
        if not isinstance(value, str) or not value.strip():
            raise TypeError(f"marketId must be a non-empty string, got {value!r}")
        v = value.strip()
        if not _OBJECT_ID_RE.match(v):
            raise TypeError(
                f"marketId must be an on-chain object id ('0x…'), got {v!r}. "
                "Tickers such as 'BTC' are not market ids -- resolve the id "
                "from /api/perpetuals/all-markets."
            )
        super().__init__(v)

    def __str__(self) -> str:
        return self._value


class SuiAddress(_Id):
    """A Sui address (``0x…``)."""

    __slots__ = ()

    def __init__(self, value: Union[str, "SuiAddress"]) -> None:
        if isinstance(value, SuiAddress):
            value = value.value
        if not isinstance(value, str) or not _OBJECT_ID_RE.match(value.strip()):
            raise TypeError(f"invalid Sui address: {value!r}")
        super().__init__(value.strip())

    def __str__(self) -> str:
        return self._value


def looks_like_object_id(value: str) -> bool:
    """True when a string looks like a resolved on-chain id rather than a ticker."""
    return bool(isinstance(value, str) and _OBJECT_ID_RE.match(value.strip()))


# ── Native BigInt wire format ────────────────────────────────────
# gotchas.md §11: native BigInt fields require the exact "…n" string on request
# AND return it on response. Plain numbers are rejected. Other timestamps and
# counters remain ordinary JSON numbers -- never blanket-convert a payload.


def to_native_bigint(value: Union[int, NativeAccountId]) -> str:
    """Encode for a native BigInt request field: ``123`` -> ``"123n"``."""
    if isinstance(value, NativeAccountId):
        return value.wire()
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an int for BigInt encoding, got {value!r}")
    return f"{value}n"


def from_native_bigint(value: str) -> int:
    """Decode a native BigInt response field: ``"123n"`` -> ``123``."""
    if not isinstance(value, str):
        raise TypeError(f'expected native BigInt wire format ("123n"), got {value!r}')
    m = _NATIVE_BIGINT_RE.match(value.strip())
    if not m:
        raise TypeError(f'expected native BigInt wire format ("123n"), got {value!r}')
    return int(m.group(1))


def is_native_bigint(value: object) -> bool:
    """True if a value is already in native BigInt wire format."""
    return isinstance(value, str) and bool(_NATIVE_BIGINT_RE.match(value.strip()))


def coerce_bigint(value: object) -> int:
    """Accept either ``"123n"`` or a plain int/numeric string and return an int.

    For *reading* responses, where a field's exact transport is ambiguous.
    Never use this to build a request -- requests must use
    :func:`to_native_bigint` so the ``"n"`` suffix is always present.
    """
    if isinstance(value, bool):
        raise TypeError("bool is not a BigInt")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if is_native_bigint(s):
            return from_native_bigint(s)
        if s.lstrip("-").isdigit():
            return int(s)
    raise TypeError(f"cannot read {value!r} as a BigInt")


# ── Order side / type encodings (from the live spec) ──────────────
# side:      0 = bid (long), 1 = ask (short)
# orderType: 0 = GTC, 1 = FOK, 2 = Post-Only, 3 = IOC

SIDE_BID = 0
SIDE_ASK = 1

ORDER_TYPE_GTC = 0
ORDER_TYPE_FOK = 1
ORDER_TYPE_POST_ONLY = 2
ORDER_TYPE_IOC = 3

#: Trigger price reference (v3.0.0): 0 index, 1 book mid, 2 mark.
TRIGGER_PRICE_INDEX = 0
TRIGGER_PRICE_BOOK_MID = 1
TRIGGER_PRICE_MARK = 2


def side_to_int(side: str) -> int:
    """Map the engine's ``"buy"``/``"sell"`` onto the API's numeric side."""
    s = str(side).strip().lower()
    if s in ("buy", "bid", "long", "b"):
        return SIDE_BID
    if s in ("sell", "ask", "short", "s"):
        return SIDE_ASK
    raise ValueError(f"unknown side {side!r}; expected 'buy' or 'sell'")


def int_to_side(value: int) -> str:
    return "buy" if int(value) == SIDE_BID else "sell"


def order_type_from_tif(tif: str) -> int:
    """Map the engine's time-in-force vocabulary onto the API's ``orderType``.

    ``Alo`` ("add liquidity only") is the engine's maker-only flag and maps to
    Post-Only. Getting this wrong turns a market-making quote into a taker order
    and silently inverts the strategy's fee economics.
    """
    t = str(tif).strip().lower()
    if t in ("gtc", "limit"):
        return ORDER_TYPE_GTC
    if t in ("fok",):
        return ORDER_TYPE_FOK
    if t in ("alo", "postonly", "post_only", "post-only", "maker"):
        return ORDER_TYPE_POST_ONLY
    if t in ("ioc", "market", "taker"):
        return ORDER_TYPE_IOC
    raise ValueError(f"unknown time-in-force {tif!r}")
