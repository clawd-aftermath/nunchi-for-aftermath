"""Aftermath Perpetuals proxy — drop-in replacement for DirectHLProxy.

Drop-in replacement for ``cli.hl_adapter.DirectHLProxy`` that routes all
order flow through Aftermath Finance perpetuals on Sui instead of Hyperliquid.

Instrument naming convention (AF-style):
  BTC-AF-PERP  ->  Aftermath BTC perp
  ETH-AF-PERP  ->  Aftermath ETH perp
  XAG-AF-PERP  ->  Aftermath XAG (Silver) perp
  SUI-AF-PERP  ->  Aftermath SUI perp

If you pass plain HL-style names (``ETH-PERP``, ``BTC-PERP``) they are
accepted too — the proxy normalises them automatically.

Signing pipeline (mirrors TypeScript orders.ts):
  1. POST /api/perpetuals/account/transactions/place-limit-order  -> { txKind }
  2. Transaction.fromKind(base64_decode(txKind))                  -> tx
  3. SuiClient.signAndExecuteTransaction(tx, keypair)             -> digest

Required env vars:
  SUI_PRIVATE_KEY   - Bech32 (suiprivkey1...) or Base64 Ed25519 key
  AF_BASE_URL       - Aftermath API base (default: https://aftermath.finance)
  AF_COLLATERAL_TYPE - USDC coin type (default: 0xdba3...::usdc::USDC)
  AF_SUI_RPC        - Sui fullnode RPC URL (default: auto from AF_BASE_URL)

Optional:
  AF_ACCOUNT_NUMBER - Override numeric account ID (auto-discovered if unset)
  AF_LEVERAGE       - Default leverage for new positions (default: 5)
  AFTERMATH_WRITE_SETTLE_MS - ms to wait after each Sui write (default: 2000)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import requests

from common.models import MarketSnapshot
from parent.hl_proxy import HLFill

log = logging.getLogger("af_proxy")

ZERO = Decimal("0")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AF_BASE_URL_DEFAULT = "https://aftermath.finance"
AF_COLLATERAL_TYPE_DEFAULT = (
    "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"
)
AF_LEVERAGE_DEFAULT = 5
WRITE_SETTLE_MS_DEFAULT = 2_000
AUTO_COLLATERAL_ALLOCATE_AMOUNT_DEFAULT = 100.0

# Gas pool / sponsorship defaults
# Set AF_SPONSOR_ADDRESS to enable gasless trading via GasPool.
# When set, all trading transactions include a `sponsor` field so the
# agent wallet never needs SUI for gas.
AF_SPONSOR_ADDRESS_DEFAULT = ""  # empty = no sponsorship


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_url() -> str:
    return os.environ.get("AF_BASE_URL", AF_BASE_URL_DEFAULT).rstrip("/")


def _settle_ms() -> int:
    raw = os.environ.get("AFTERMATH_WRITE_SETTLE_MS")
    if raw:
        try:
            v = int(raw)
            return max(0, v)
        except ValueError:
            pass
    return WRITE_SETTLE_MS_DEFAULT


def _to_native_int(value: float) -> str:
    """Convert float USD price/size to 9-decimal BigInt string (e.g. '65000500000000n')."""
    return f"{int(round(value * 1e9))}n"


def _order_type_from_tif(tif: str) -> int:
    # GTC=0, FOK=1, PostOnly=2, IOC=3
    order_type_map = {"gtc": 0, "fok": 1, "postonly": 2, "alo": 2, "ioc": 3}
    return order_type_map.get(tif.lower(), 0)


# ---------------------------------------------------------------------------
# HTTP retry helper (retries 429/5xx/timeout, 3 attempts, exponential backoff)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 0.3  # seconds


def _is_retryable(exc: Exception) -> bool:
    """Check if an HTTP error is transient and should be retried."""
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        if code == 429 or (500 <= code < 600):
            return True
    return False


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Make an HTTP request with retry logic for transient failures."""
    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.request(method, url, **kwargs)
            # Raise for 4xx/5xx so we can decide to retry
            if resp.status_code == 429 or resp.status_code >= 500:
                resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1 and _is_retryable(exc):
                delay = _RETRY_BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "Request %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    method, url.split("/api/")[-1], attempt + 1, _MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def _normalise_instrument(instrument: str) -> str:
    """Normalise HL-style names to AF-style.

    ETH-PERP  -> ETH-AF-PERP
    ETH       -> ETH-AF-PERP
    ETH-AF-PERP -> ETH-AF-PERP  (passthrough)
    """
    s = instrument.upper()
    # Already AF-style
    if s.endswith("-AF-PERP"):
        return s
    # Strip known HL suffixes
    for suffix in ("-PERP", "-perp", "/USD:USD"):
        if s.endswith(suffix.upper()):
            s = s[: -len(suffix)]
            break
    return f"{s}-AF-PERP"


def _base_asset(instrument: str) -> str:
    """Extract base asset name: ETH-AF-PERP -> ETH."""
    s = _normalise_instrument(instrument)
    return s.replace("-AF-PERP", "")


# ---------------------------------------------------------------------------
# Market registry: maps base asset -> chId (ClearingHouse object ID)
# Fetched lazily from /api/ccxt/markets and cached for 60 s.
# ---------------------------------------------------------------------------

_markets_cache: Dict[str, Any] = {}  # base -> {chId, base, precision, ...}
_markets_ts: float = 0.0
_markets_lock = threading.Lock()
MARKETS_TTL = 60.0

_all_markets_cache: Dict[str, Any] = {}  # marketId -> market state
_all_markets_ts: float = 0.0
_all_markets_lock = threading.Lock()
ALL_MARKETS_TTL = 60.0

_position_cache: Dict[str, bool] = {}  # base_asset -> has_position
_position_cache_ts: float = 0.0
_position_cache_lock = threading.Lock()
POSITION_CACHE_TTL = 10.0


def _fetch_markets(base_url: str) -> Dict[str, Any]:
    """Fetch all Aftermath markets keyed by base asset."""
    resp = _request_with_retry("GET", f"{base_url}/api/ccxt/markets", timeout=15)
    resp.raise_for_status()
    raw: Dict[str, Any] = resp.json()
    result: Dict[str, Any] = {}
    for mkt in raw.values():
        if mkt.get("active") and mkt.get("swap"):
            base = mkt["base"].upper()
            result[base] = {
                "chId": mkt["id"],
                "symbol": mkt["symbol"],
                "base": base,
                "tickSize": mkt.get("precision", {}).get("price", 0.01),
                "minSize": mkt.get("limits", {}).get("amount", {}).get("min", 0.001),
                "raw": mkt,
            }
    return result


def _markets(base_url: str) -> Dict[str, Any]:
    global _markets_cache, _markets_ts
    with _markets_lock:
        now = time.time()
        if now - _markets_ts > MARKETS_TTL or not _markets_cache:
            _markets_cache = _fetch_markets(base_url)
            _markets_ts = now
        return _markets_cache


def _fetch_all_markets(base_url: str) -> Dict[str, Any]:
    """Fetch all market states keyed by marketId."""
    collateral_coin_type = os.environ.get("AF_COLLATERAL_TYPE", AF_COLLATERAL_TYPE_DEFAULT)
    resp = _request_with_retry(
        "POST",
        f"{base_url}/api/perpetuals/all-markets",
        json={"collateralCoinType": collateral_coin_type},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()
    markets = raw.get("marketStates") if isinstance(raw, dict) else raw
    result: Dict[str, Any] = {}
    if isinstance(markets, dict):
        for market_id, market_state in markets.items():
            if isinstance(market_state, dict):
                result[market_id] = market_state
    elif isinstance(markets, list):
        for item in markets:
            if not isinstance(item, dict):
                continue
            market_id = item.get("marketId") or item.get("id")
            if market_id:
                result[str(market_id)] = item
    return result


def _all_markets(base_url: str) -> Dict[str, Any]:
    global _all_markets_cache, _all_markets_ts
    with _all_markets_lock:
        now = time.time()
        if now - _all_markets_ts > ALL_MARKETS_TTL or not _all_markets_cache:
            _all_markets_cache = _fetch_all_markets(base_url)
            _all_markets_ts = now
        return _all_markets_cache


def _invalidate_position_cache() -> None:
    global _position_cache_ts
    with _position_cache_lock:
        _position_cache_ts = 0.0


def _get_market(base_url: str, instrument: str) -> Dict[str, Any]:
    base = _base_asset(instrument)
    mkt = _markets(base_url).get(base)
    if not mkt:
        raise ValueError(
            f"Aftermath market not found for base '{base}' (instrument={instrument}). "
            f"Available: {sorted(_markets(base_url).keys())}"
        )
    return mkt


# ---------------------------------------------------------------------------
# Account discovery
# ---------------------------------------------------------------------------

_account_cache: Optional[Dict[str, Any]] = None
_account_lock = threading.Lock()


def _fetch_account_info(base_url: str, wallet_address: str) -> Dict[str, Any]:
    """Fetch account info and return {accountCapId, accountNumber, walletAddress}.

    Uses CCXT /api/ccxt/accounts to match the TS market maker pattern:
    finds type=capability -> accountCapId, type=account -> accountNumber.
    Falls back to native /api/perpetuals/accounts/owned if CCXT fails.
    """
    # Try CCXT accounts first (matches TS market maker pattern)
    try:
        resp = _request_with_retry(
            "POST",
            f"{base_url}/api/ccxt/accounts",
            json={"address": wallet_address},
            timeout=15,
        )
        resp.raise_for_status()
        accounts = resp.json()
        if isinstance(accounts, list) and accounts:
            cap = next((a for a in accounts if a.get("type") == "capability"), None)
            if cap:
                return {
                    "accountCapId": cap["id"],
                    "accountNumber": int(cap.get("accountNumber", 0)),
                    "walletAddress": wallet_address,
                }
    except Exception as e:
        log.debug("CCXT accounts lookup failed, trying native: %s", e)

    # Fallback: native endpoint
    resp = _request_with_retry(
        "POST",
        f"{base_url}/api/perpetuals/accounts/owned",
        json={"walletAddress": wallet_address},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    caps = data.get("accountCaps", [])
    if not caps:
        raise RuntimeError(
            f"No Aftermath perpetuals account found for wallet {wallet_address}. "
            "Create one via the Aftermath UI first."
        )
    cap = caps[0]
    # accountId may be numeric or BigInt-string like "123n"
    acc_id = cap.get("accountId", "0")
    if isinstance(acc_id, str):
        acc_id = int(acc_id.rstrip("n"))
    else:
        acc_id = int(acc_id)
    return {
        "accountNumber": acc_id,
        "accountCapId": cap.get("id", ""),
        "walletAddress": wallet_address,
    }


def _account(base_url: str, wallet_address: str) -> Dict[str, Any]:
    global _account_cache
    with _account_lock:
        # Respect env override
        override = os.environ.get("AF_ACCOUNT_NUMBER")
        if override:
            return {"accountNumber": int(override), "walletAddress": wallet_address}
        if _account_cache and _account_cache.get("walletAddress") == wallet_address:
            return _account_cache
        _account_cache = _fetch_account_info(base_url, wallet_address)
        log.info("AF account discovered: number=%s", _account_cache["accountNumber"])
        return _account_cache


# ---------------------------------------------------------------------------
# Sui signing (pure Python via pysui or nacl)
# ---------------------------------------------------------------------------

def _get_keypair(private_key: str):
    """Return a pysui SyncKeyPair from a Bech32 or base64 private key.

    Falls back to manual nacl signing if pysui is unavailable.
    """
    try:
        from pysui.sui.sui_crypto import SuiKeyPair
        return SuiKeyPair.from_private_key(private_key)
    except ImportError:
        pass
    # Minimal fallback using PyNaCl
    import nacl.signing  # type: ignore[import-untyped]
    if private_key.startswith("suiprivkey1"):
        seed = _bech32_to_seed(private_key)
    else:
        raw = base64.b64decode(private_key)
        seed = raw[:32]
    return nacl.signing.SigningKey(seed)


def _bech32_to_seed(key: str) -> bytes:
    """Decode a Sui bech32 private key (suiprivkey1...) to 32-byte seed."""
    from bech32 import bech32_decode, convertbits  # type: ignore[import-untyped]
    hrp, data = bech32_decode(key)
    if data is None:
        raise ValueError(f"Invalid bech32 key: {key}")
    decoded = convertbits(data, 5, 8, False)
    if decoded is None or len(decoded) < 33:
        raise ValueError("Failed to decode bech32 key")
    # First byte is key type flag; skip it
    return bytes(decoded[1:33])


def _wallet_address_from_key(private_key: str) -> str:
    """Derive Sui wallet address from private key.

    Tries Node.js helper first (works without PyNaCl), falls back to pure-Python nacl.
    """
    # Try Node.js helper — works even without PyNaCl
    try:
        return _wallet_address_via_node(private_key)
    except Exception:
        pass

    # Fallback: pure-Python via PyNaCl
    try:
        import nacl.signing  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "Cannot derive wallet address: Node.js helper failed and PyNaCl is not installed. "
            "Install PyNaCl (`pip install pynacl`) or ensure Node.js + @mysten/sui are available."
        )
    if private_key.startswith("suiprivkey1"):
        seed = _bech32_to_seed(private_key)
    else:
        raw = base64.b64decode(private_key)
        seed = raw[:32]
    sk = nacl.signing.SigningKey(seed)
    vk = sk.verify_key
    # Sui Ed25519 address: Blake2b-256 of (0x00 || pubkey)
    import hashlib
    flag_and_pubkey = bytes([0x00]) + bytes(vk)
    digest = hashlib.blake2b(flag_and_pubkey, digest_size=32).digest()
    return "0x" + digest.hex()


def _wallet_address_via_node(private_key: str) -> str:
    """Derive wallet address using the Node.js signer (--address mode)."""
    import subprocess
    script = _get_node_signer_script()
    result = subprocess.run(
        ["node", script, "--address"],
        input=json.dumps({"privateKey": private_key}),
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Node address derivation failed: {result.stderr}")
    data = json.loads(result.stdout.strip())
    return data["address"]


def _sponsor_address() -> str:
    """Return the gas pool sponsor wallet address, or empty string if not set."""
    return os.environ.get("AF_SPONSOR_ADDRESS", AF_SPONSOR_ADDRESS_DEFAULT).strip()


def _add_sponsor_to_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """If a sponsor address is configured, add the ``sponsor`` field to a
    transaction request body so the API returns a sponsored ``txKind`` +
    ``sponsorSignature``.  This enables gasless trading via GasPool — the
    agent wallet never needs SUI for gas.
    """
    sponsor = _sponsor_address()
    if sponsor:
        body["sponsor"] = {"walletAddress": sponsor}
    return body


def _sign_and_submit(
    private_key: str,
    tx_kind_b64: str,
    base_url: str,
    rpc_url: str,
    sponsor_signature: Optional[str] = None,
) -> str:
    """Sign and submit a Sui TransactionKind. Returns digest.

    Tries Node.js helper first (most reliable), then pysui, then nacl fallback.

    If *sponsor_signature* is provided (from a GasPool-sponsored response),
    it is passed to the Node.js signer so both the agent's signature and the
    sponsor's signature are submitted together.
    """
    tx_kind_bytes = base64.b64decode(tx_kind_b64)

    # 1. Try Node.js helper (always works if node + @mysten/sui installed)
    try:
        return _node_sign_submit(private_key, tx_kind_b64, rpc_url, sponsor_signature=sponsor_signature)
    except FileNotFoundError:
        log.debug("Node signer script not found, trying pysui/nacl fallback")
    except Exception as e:
        log.warning("Node signer failed, trying pysui/nacl fallback: %s", e)

    # 2. Try pysui
    try:
        return _pysui_sign_submit(private_key, tx_kind_bytes, rpc_url)
    except ImportError:
        pass

    # 3. Try nacl fallback
    try:
        import nacl.signing  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "Cannot sign transaction: Node.js signer failed, pysui not installed, "
            "and PyNaCl not installed. Install at least one signing backend."
        )
    if private_key.startswith("suiprivkey1"):
        seed = _bech32_to_seed(private_key)
    else:
        raw = base64.b64decode(private_key)
        seed = raw[:32]
    sk = nacl.signing.SigningKey(seed)
    vk = sk.verify_key
    return _requests_sign_submit(sk, vk, tx_kind_bytes, rpc_url)


def _pysui_sign_submit(private_key: str, tx_kind_bytes: bytes, rpc_url: str) -> str:
    """Sign and submit using pysui SDK."""
    from pysui import SuiConfig, SyncClient  # type: ignore[import-untyped]
    from pysui.sui.sui_txn import SyncTransaction  # type: ignore[import-untyped]

    cfg = SuiConfig.user_config(rpc_url=rpc_url, prv_keys=[private_key])
    client = SyncClient(cfg)
    signer = cfg.active_address

    # Wrap txKind bytes into a Transaction
    from pysui.sui.sui_types.bcs import TransactionKind  # type: ignore[import-untyped]
    from pysui.abstracts.client_keypair import KeyPair  # type: ignore[import-untyped]

    result = client.sign_and_submit(
        signer=signer,
        tx_bytes=base64.b64encode(tx_kind_bytes).decode(),
    )
    if hasattr(result, "digest"):
        return result.digest
    raise RuntimeError(f"pysui submission failed: {result}")


def _node_sign_submit(
    private_key: str,
    tx_kind_b64: str,
    rpc_url: str,
    sponsor_signature: Optional[str] = None,
) -> str:
    """Sign and submit via Node.js helper using stdin (no tmpfile).

    When *sponsor_signature* is provided (from a GasPool-sponsored response),
    it is included in the payload so the Node.js helper can submit the
    transaction with both the agent's signature and the sponsor's signature.
    """
    import subprocess
    script = _get_node_signer_script()
    payload_dict: Dict[str, Any] = {
        "txKind": tx_kind_b64,
        "privateKey": private_key,
        "rpcUrl": rpc_url,
    }
    if sponsor_signature:
        payload_dict["sponsorSignature"] = sponsor_signature
    payload = json.dumps(payload_dict)
    result = subprocess.run(
        ["node", script, "--stdin"],
        input=payload,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Node signer failed: {result.stderr}")
    data = json.loads(result.stdout.strip())
    return data["digest"]


def _requests_sign_submit(sk, vk, tx_kind_bytes: bytes, rpc_url: str) -> str:
    """Sign and submit via raw Sui JSON-RPC (no pysui).

    This is a last-resort fallback. In practice, the Node.js helper or pysui
    are preferred because constructing BCS TransactionData from scratch in
    pure Python is fragile.
    """
    # This fallback still delegates to the Node.js helper — if it's not
    # available, we've already exhausted all options in _sign_and_submit.
    raise RuntimeError(
        "Pure-Python BCS signing is not implemented. "
        "Use the Node.js signer (npm install @mysten/sui in cli/) or install pysui."
    )


def _get_node_signer_script() -> str:
    """Return path to the bundled Node.js signing helper."""
    script_path = os.path.join(os.path.dirname(__file__), "_af_node_signer.mjs")
    if not os.path.exists(script_path):
        raise FileNotFoundError(
            f"Node.js signer not found at {script_path}. "
            "Run: cd cli && npm install @mysten/sui"
        )
    return script_path


# ---------------------------------------------------------------------------
# Serialised write queue (prevents stale-object races)
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Orderbook snapshot
# ---------------------------------------------------------------------------

def _fetch_snapshot(base_url: str, instrument: str) -> MarketSnapshot:
    """Fetch current orderbook snapshot from Aftermath."""
    mkt = _get_market(base_url, instrument)
    ch_id = mkt["chId"]

    resp = _request_with_retry(
        "POST",
        f"{base_url}/api/perpetuals/markets/orderbooks",
        json={"marketIds": [ch_id]},
        timeout=10,
    )
    resp.raise_for_status()
    books = resp.json()

    # Response shape: list of { marketId, bids: [[price, size], ...], asks: [...] }
    # or dict keyed by marketId
    if isinstance(books, dict):
        book = books.get(ch_id, {})
    elif isinstance(books, list):
        book = next((b for b in books if b.get("marketId") == ch_id), {})
    else:
        book = {}

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    # Sort to get best bid/ask
    if bids:
        bids_sorted = sorted(bids, key=lambda x: -float(x[0]))
        best_bid = float(bids_sorted[0][0])
    else:
        best_bid = 0.0

    if asks:
        asks_sorted = sorted(asks, key=lambda x: float(x[0]))
        best_ask = float(asks_sorted[0][0])
    else:
        best_ask = 0.0

    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    spread_bps = ((best_ask - best_bid) / mid * 10_000) if mid > 0 else 0.0

    # Fetch 24h stats for volume/open interest
    funding_rate = 0.0
    volume_24h = 0.0
    open_interest = 0.0
    try:
        stats_resp = _request_with_retry(
            "POST",
            f"{base_url}/api/perpetuals/markets/24hr-stats",
            json={"marketIds": [ch_id]},
            timeout=5,
        )
        if stats_resp.ok:
            stats = stats_resp.json()
            if isinstance(stats, dict):
                s = stats.get(ch_id, {})
            elif isinstance(stats, list):
                s = next((x for x in stats if x.get("marketId") == ch_id), {})
            else:
                s = {}
            volume_24h = float(s.get("volume24h", 0) or 0)
            open_interest = float(s.get("openInterest", 0) or 0)
    except Exception:
        pass

    # Gotcha #18: 24hr-stats fundingRate is inflated. Use all-markets premiumTwap/indexPrice.
    try:
        market_state = _all_markets(base_url).get(ch_id, {})
        premium_twap = float(market_state.get("premiumTwap", 0) or 0)
        index_price = float(market_state.get("indexPrice", 0) or 0)
        if index_price > 0:
            funding_rate = premium_twap / index_price
    except Exception:
        pass

    return MarketSnapshot(
        instrument=_normalise_instrument(instrument),
        mid_price=round(mid, 6),
        bid=round(best_bid, 6),
        ask=round(best_ask, 6),
        spread_bps=round(spread_bps, 4),
        timestamp_ms=int(time.time() * 1000),
        volume_24h=volume_24h,
        funding_rate=funding_rate,
        open_interest=open_interest,
    )


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def _has_position(base_url: str, account_number: int, symbol: str) -> bool:
    """Check if an on-chain position record exists for this market."""
    global _position_cache, _position_cache_ts
    base = _base_asset(symbol).upper()

    with _position_cache_lock:
        now = time.time()
        if now - _position_cache_ts <= POSITION_CACHE_TTL and base in _position_cache:
            return _position_cache[base]

    try:
        resp = _request_with_retry(
            "POST",
            f"{base_url}/api/ccxt/positions",
            json={"accountNumber": account_number},
            timeout=10,
        )
        resp.raise_for_status()
        positions = resp.json()
        next_cache: Dict[str, bool] = {}
        for m_base in _markets(base_url).keys():
            next_cache[m_base] = False

        for p in positions:
            sym = p.get("symbol", "")
            sym_upper = sym.upper()
            contracts = p.get("contracts", 0) or 0
            collateral = p.get("collateral", 0) or 0
            has_pos = contracts != 0 or collateral > 0
            if not has_pos:
                continue
            for m_base in list(next_cache.keys()):
                if m_base in sym_upper:
                    next_cache[m_base] = True

        with _position_cache_lock:
            _position_cache = next_cache
            _position_cache_ts = time.time()
            return _position_cache.get(base, False)
    except Exception as e:
        log.warning("hasPosition check failed for %s: %s", symbol, e)
        with _position_cache_lock:
            if base in _position_cache and time.time() - _position_cache_ts <= POSITION_CACHE_TTL:
                return _position_cache[base]
    return False


def _preview_limit_order(
    base_url: str,
    account_number: int,
    ch_id: str,
    native_side: int,
    size: float,
    price: float,
    order_type: int,
    leverage: Optional[int] = None,
) -> Dict[str, Any]:
    """Preview a limit order to get collateralChange and hasPosition."""
    body: Dict[str, Any] = {
        "accountId": account_number,
        "marketId": ch_id,
        "side": native_side,
        "price": _to_native_int(price),
        "size": _to_native_int(size),
        "orderType": order_type,
        "reduceOnly": False,
    }
    if leverage is not None:
        body["leverage"] = float(leverage)

    resp = _request_with_retry(
        "POST",
        f"{base_url}/api/perpetuals/account/previews/place-limit-order",
        json=body,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        log.warning("Preview error: %s", data["error"])
        return {"collateralChange": 0, "hasPosition": False, "cancelSlTp": False}
    return data


def _place_order_native(
    base_url: str,
    private_key: str,
    rpc_url: str,
    wallet_address: str,
    account_number: int,
    instrument: str,
    side: str,
    size: float,
    price: float,
    tif: str = "Gtc",
    leverage: Optional[int] = None,
) -> Optional[str]:
    """Place a limit order via native perpetuals endpoint. Returns tx digest."""
    mkt = _get_market(base_url, instrument)
    ch_id = mkt["chId"]
    native_side = 0 if side.lower() == "buy" else 1

    order_type = _order_type_from_tif(tif)

    # For PostOnly orders, collateralChange=0 (collateral must be pre-allocated).
    # For GTC/IOC/FOK orders, preview first to get the correct collateralChange
    # (critical for first-trade on a market with no existing position/collateral).
    if order_type == 2:  # PostOnly
        has_pos = _has_position(base_url, account_number, instrument)
        collateral_change = 0
        cancel_sl_tp = False
    else:
        preview = _preview_limit_order(
            base_url, account_number, ch_id, native_side, size, price, order_type, leverage
        )
        collateral_change = preview.get("collateralChange", 0)
        has_pos = preview.get("hasPosition", _has_position(base_url, account_number, instrument))
        cancel_sl_tp = preview.get("cancelSlTp", False)

    body: Dict[str, Any] = {
        "accountId": f"{account_number}n",
        "walletAddress": wallet_address,
        "marketId": ch_id,
        "side": native_side,
        "price": _to_native_int(price),
        "size": _to_native_int(size),
        "orderType": order_type,
        "collateralChange": collateral_change,
        "hasPosition": has_pos,
        "reduceOnly": False,
        "cancelSlTp": cancel_sl_tp,
    }
    if leverage is not None:
        body["leverage"] = float(leverage)
    _add_sponsor_to_body(body)

    resp = _request_with_retry(
        "POST",
        f"{base_url}/api/perpetuals/account/transactions/place-limit-order",
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"AF place-limit-order error: {data['error']}")

    tx_kind_b64 = data.get("txKind")
    if not tx_kind_b64:
        raise RuntimeError("AF place-limit-order returned no txKind")

    digest = _sign_and_submit(
        private_key, tx_kind_b64, base_url, rpc_url,
        sponsor_signature=data.get("sponsorSignature"),
    )
    return digest


def _cancel_orders_native(
    base_url: str,
    private_key: str,
    rpc_url: str,
    wallet_address: str,
    account_number: int,
    instrument: str,
    order_ids: List[str],
) -> Optional[str]:
    """Cancel orders via native cancel-and-place-orders (empty place list)."""
    if not order_ids:
        return None

    mkt = _get_market(base_url, instrument)
    ch_id = mkt["chId"]

    has_pos = _has_position(base_url, account_number, instrument)

    # Convert string IDs to BigInt-strings
    native_ids = [f"{oid.rstrip('n')}n" for oid in order_ids if oid.isdigit() or oid.rstrip("n").isdigit()]

    body: Dict[str, Any] = {
        "accountId": f"{account_number}n",
        "walletAddress": wallet_address,
        "marketId": ch_id,
        "orderIdsToCancel": native_ids,
        "ordersToPlace": [],
        "orderType": 2,  # PostOnly
        "reduceOnly": False,
        "hasPosition": has_pos,
    }
    _add_sponsor_to_body(body)

    resp = _request_with_retry(
        "POST",
        f"{base_url}/api/perpetuals/account/transactions/cancel-and-place-orders",
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"AF cancel-orders error: {data['error']}")

    tx_kind_b64 = data.get("txKind")
    if not tx_kind_b64:
        return None

    return _sign_and_submit(
        private_key, tx_kind_b64, base_url, rpc_url,
        sponsor_signature=data.get("sponsorSignature"),
    )


def _get_open_orders(base_url: str, account_number: int, instrument: str) -> List[Dict]:
    """Fetch open orders for an instrument."""
    mkt = _get_market(base_url, instrument)
    ch_id = mkt["chId"]

    try:
        resp = _request_with_retry(
            "POST",
            f"{base_url}/api/ccxt/myPendingOrders",
            json={"accountNumber": account_number, "chId": ch_id},
            timeout=10,
        )
        resp.raise_for_status()
        orders = resp.json()
        return [
            {
                "oid": str(o.get("id", "")),
                "coin": mkt["base"],
                "side": o.get("side", ""),
                "sz": str(o.get("amount", "0")),
                "limitPx": str(o.get("price", "0")),
                "timestamp": o.get("timestamp", 0),
            }
            for o in (orders if isinstance(orders, list) else [])
        ]
    except Exception as e:
        log.error("get_open_orders failed: %s", e)
        return []


def _get_account_state(base_url: str, wallet_address: str, account_cap_id: str) -> Dict:
    """Fetch account balance summary using the accountCapId.

    The CCXT /api/ccxt/balance endpoint requires {"account": accountCapId}
    (the Sui capability object ID), NOT {"address": walletAddress}.
    """
    try:
        resp = _request_with_retry(
            "POST",
            f"{base_url}/api/ccxt/balance",
            json={"account": account_cap_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        balances = data.get("balances", {})
        total_val = sum(
            float(b.get("total", 0) or 0) for b in balances.values()
        )
        free_val = sum(
            float(b.get("free", 0) or 0) for b in balances.values()
        )
        return {
            "marginSummary": {
                "accountValue": total_val,
            },
            "withdrawable": free_val,
            "address": wallet_address,
        }
    except Exception as e:
        log.error("get_account_state failed: %s", e)
        return {}


def _get_candles(base_url: str, instrument: str, interval: str, lookback_ms: int) -> List[Dict]:
    """Fetch candles from Aftermath. Returns HL-compatible candle dicts."""
    try:
        mkt = _get_market(base_url, instrument)
        ch_id = mkt["chId"]

        # Map interval string to ms
        interval_ms_map = {
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }
        interval_ms = interval_ms_map.get(interval, 3_600_000)

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - lookback_ms

        resp = _request_with_retry(
            "POST",
            f"{base_url}/api/perpetuals/market/candle-history",
            json={
                "marketId": ch_id,
                "intervalMs": interval_ms,
                "fromTimestampMs": start_ms,
                "toTimestampMs": end_ms,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        candles = raw if isinstance(raw, list) else raw.get("candles", [])

        # Convert to HL candle format: { t, o, h, l, c, v }
        result = []
        for c in candles:
            result.append({
                "t": int(c.get("timestamp", c.get("t", 0))),
                "o": str(c.get("open", c.get("o", "0"))),
                "h": str(c.get("high", c.get("h", "0"))),
                "l": str(c.get("low", c.get("l", "0"))),
                "c": str(c.get("close", c.get("c", "0"))),
                "v": str(c.get("volume", c.get("v", "0"))),
            })
        return result
    except Exception as e:
        log.warning("get_candles failed for %s: %s", instrument, e)
        return []


def _get_all_markets_hl_format(base_url: str) -> Any:
    """Return all markets in HL meta+assetCtxs format for Radar/Pulse compatibility."""
    mkts = _markets(base_url)
    try:
        all_markets_state = _all_markets(base_url)
    except Exception:
        all_markets_state = {}
    try:
        # Fetch 24hr stats for all markets
        ch_ids = [m["chId"] for m in mkts.values()]
        stats_resp = _request_with_retry(
            "POST",
            f"{base_url}/api/perpetuals/markets/24hr-stats",
            json={"marketIds": ch_ids},
            timeout=15,
        )
        stats_resp.raise_for_status()
        stats_raw = stats_resp.json()
        if isinstance(stats_raw, dict):
            stats_by_id = stats_raw
        elif isinstance(stats_raw, list):
            stats_by_id = {s["marketId"]: s for s in stats_raw if "marketId" in s}
        else:
            stats_by_id = {}
    except Exception:
        stats_by_id = {}

    universe = []
    asset_ctxs = []
    for base, mkt in mkts.items():
        universe.append({"name": base, "szDecimals": 4})
        ch_id = mkt["chId"]
        s = stats_by_id.get(ch_id, {})
        market_state = all_markets_state.get(ch_id, {})
        premium_twap = float(market_state.get("premiumTwap", 0) or 0)
        index_price = float(market_state.get("indexPrice", 0) or 0)
        funding = (premium_twap / index_price) if index_price > 0 else 0.0
        mark_px = market_state.get("markPrice") or s.get("markPrice", "0") or "0"
        asset_ctxs.append({
            "funding": str(funding),
            "openInterest": str(market_state.get("openInterest", s.get("openInterest", "0")) or "0"),
            "prevDayPx": str(market_state.get("indexPrice24hrsAgo", s.get("prevDayPx", "0")) or "0"),
            "dayNtlVlm": str(s.get("volume24h", "0") or "0"),
            "markPx": str(mark_px),
        })
    return [{"universe": universe}, asset_ctxs]


def _get_all_mids(base_url: str) -> Dict[str, str]:
    """Return mid prices for all markets keyed by base asset."""
    mkts = _markets(base_url)
    ch_ids = [m["chId"] for m in mkts.values()]
    mids: Dict[str, str] = {}

    try:
        resp = _request_with_retry(
            "POST",
            f"{base_url}/api/perpetuals/markets/prices",
            json={"marketIds": ch_ids},
            timeout=10,
        )
        resp.raise_for_status()
        prices = resp.json()
        if isinstance(prices, dict):
            for base, mkt in mkts.items():
                ch_id = mkt["chId"]
                p = prices.get(ch_id, {})
                mark = p.get("markPrice") or p.get("indexPrice") or "0"
                mids[base] = str(mark)
        elif isinstance(prices, list):
            for item in prices:
                ch_id = item.get("marketId", "")
                for base, mkt in mkts.items():
                    if mkt["chId"] == ch_id:
                        mids[base] = str(item.get("markPrice") or item.get("price") or "0")
    except Exception as e:
        log.warning("get_all_mids failed: %s", e)

    return mids


# ---------------------------------------------------------------------------
# RPC URL resolution
# ---------------------------------------------------------------------------

def _rpc_url() -> str:
    url = os.environ.get("AF_SUI_RPC")
    if url:
        return url
    base = _base_url()
    if "testnet" in base:
        return "https://fullnode.testnet.sui.io:443"
    return "https://fullnode.mainnet.sui.io:443"


# ---------------------------------------------------------------------------
# Main proxy class
# ---------------------------------------------------------------------------


class AftermathProxy:
    """Aftermath Perpetuals trading proxy — implements the same interface as
    ``DirectHLProxy`` so it can be used as a drop-in replacement in
    ``TradingEngine``, ``OrderManager``, and any APEX/Guard/Radar/Pulse code.

    Usage in run.py::

        from cli.af_proxy import AftermathProxy
        hl = AftermathProxy()

        engine = TradingEngine(
            hl=hl,
            instrument="ETH-AF-PERP",
            ...
        )
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        base_url: Optional[str] = None,
        rpc_url: Optional[str] = None,
    ):
        self._private_key = private_key or os.environ.get("SUI_PRIVATE_KEY", "")
        if not self._private_key:
            raise ValueError("SUI_PRIVATE_KEY is required for AftermathProxy")

        self._base_url = (base_url or _base_url()).rstrip("/")
        self._rpc_url = rpc_url or _rpc_url()
        self._wallet_address = _wallet_address_from_key(self._private_key)
        self._leverage: int = int(os.environ.get("AF_LEVERAGE", AF_LEVERAGE_DEFAULT))
        self._collateral_allocated: set = set()

        self._sponsor = _sponsor_address()

        log.info(
            "AftermathProxy initialised: wallet=%s... base=%s sponsor=%s",
            self._wallet_address[:10],
            self._base_url,
            self._sponsor[:10] + "..." if self._sponsor else "none (agent pays gas)",
        )

    # -- Internal helpers --

    def _account_number(self) -> int:
        return _account(self._base_url, self._wallet_address)["accountNumber"]

    def _ensure_collateral_allocated(self, instrument: str, tif: str = "Ioc") -> None:
        """Auto-allocate collateral before the first PostOnly order on a market."""
        if _order_type_from_tif(tif) != 2:
            return
        base = _base_asset(instrument)
        if base in self._collateral_allocated:
            return

        amount_raw = os.environ.get("AF_COLLATERAL_ALLOCATE_AMOUNT")
        try:
            amount = float(amount_raw) if amount_raw else AUTO_COLLATERAL_ALLOCATE_AMOUNT_DEFAULT
        except ValueError:
            amount = AUTO_COLLATERAL_ALLOCATE_AMOUNT_DEFAULT

        digest = self.allocate_collateral(instrument, amount)
        if digest:
            self._collateral_allocated.add(base)

    # -- Interface methods (matching DirectHLProxy) --

    def get_snapshot(self, instrument: str = "ETH-AF-PERP") -> MarketSnapshot:
        """Fetch current orderbook snapshot."""
        try:
            return _fetch_snapshot(self._base_url, instrument)
        except Exception as e:
            log.error("get_snapshot failed for %s: %s", instrument, e)
            return MarketSnapshot(instrument=_normalise_instrument(instrument))

    def _account_cap_id(self) -> str:
        return _account(self._base_url, self._wallet_address).get("accountCapId", "")

    def get_account_state(self) -> Dict:
        """Fetch account value / margin summary."""
        cap_id = self._account_cap_id()
        if not cap_id:
            log.warning("No accountCapId found, account_state will be empty")
            return {}
        return _get_account_state(self._base_url, self._wallet_address, cap_id)

    def set_leverage(self, leverage: int, coin: str = "ETH", is_cross: bool = True) -> None:
        """Set leverage for a market. Queues a set-leverage transaction."""
        self._leverage = leverage
        try:
            base = _base_asset(coin)
            # Map coin to instrument for market lookup
            instrument = f"{base}-AF-PERP"
            mkt = _get_market(self._base_url, instrument)
            ch_id = mkt["chId"]
            acc_num = self._account_number()

            body = {
                "accountId": f"{acc_num}n",
                "walletAddress": self._wallet_address,
                "marketId": ch_id,
                "leverage": float(leverage),
                "collateralChange": 0,
            }
            resp = _request_with_retry(
                "POST",
                f"{self._base_url}/api/perpetuals/account/transactions/set-leverage",
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                log.warning("set-leverage error: %s", data["error"])
                return
            tx_kind_b64 = data.get("txKind")
            if tx_kind_b64:
                with _write_lock:
                    digest = _sign_and_submit(
                        self._private_key, tx_kind_b64, self._base_url, self._rpc_url
                    )
                    time.sleep(_settle_ms() / 1000.0)
                log.info("Leverage set to %dx for %s: %s", leverage, base, digest)
        except Exception as e:
            log.warning("set_leverage failed for %s: %s", coin, e)

    def allocate_collateral(self, instrument: str, amount: float) -> Optional[str]:
        """Allocate collateral for a market before PostOnly quoting."""
        try:
            mkt = _get_market(self._base_url, instrument)
            ch_id = mkt["chId"]
            acc_num = self._account_number()
            body = {
                "accountId": f"{acc_num}n",
                "walletAddress": self._wallet_address,
                "marketId": ch_id,
                "amount": float(amount),
            }

            with _write_lock:
                resp = _request_with_retry(
                    "POST",
                    f"{self._base_url}/api/perpetuals/account/transactions/allocate-collateral",
                    json=body,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    raise RuntimeError(f"AF allocate-collateral error: {data['error']}")
                tx_kind_b64 = data.get("txKind")
                if not tx_kind_b64:
                    return None
                digest = _sign_and_submit(
                    self._private_key, tx_kind_b64, self._base_url, self._rpc_url
                )
                time.sleep(_settle_ms() / 1000.0)

            self._collateral_allocated.add(_base_asset(instrument))
            log.info("AF collateral allocated: %s %s -> %s", instrument, amount, digest)
            return digest
        except Exception as e:
            log.warning("allocate_collateral failed for %s: %s", instrument, e)
            return None

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[Dict] = None,
    ) -> Optional[HLFill]:
        """Place a single limit order on Aftermath. Returns HLFill if queued/filled.

        Note: Aftermath orders are not IOC by default. IOC is mapped to on-chain
        orderType=3 which will cancel any unfilled portion. For market-maker use
        (ALO/PostOnly) pass tif="Alo" or tif="PostOnly".
        """
        try:
            self._ensure_collateral_allocated(instrument, tif)
            acc_num = self._account_number()
            with _write_lock:
                digest = _place_order_native(
                    base_url=self._base_url,
                    private_key=self._private_key,
                    rpc_url=self._rpc_url,
                    wallet_address=self._wallet_address,
                    account_number=acc_num,
                    instrument=instrument,
                    side=side,
                    size=size,
                    price=price,
                    tif=tif,
                    leverage=self._leverage,
                )
                _invalidate_position_cache()
                time.sleep(_settle_ms() / 1000.0)

            if not digest:
                return None

            log.info("AF placed [%s]: %s %s %s @ %s -> %s", tif, side, size, instrument, price, digest)

            return HLFill(
                oid=digest,
                instrument=_normalise_instrument(instrument),
                side=side.lower(),
                price=Decimal(str(price)),
                quantity=Decimal(str(size)),
                timestamp_ms=int(time.time() * 1000),
            )
        except Exception as e:
            log.error("place_order failed: %s %s %s @ %s: %s", side, size, instrument, price, e)
            return None

    def cancel_and_place_orders(
        self,
        instrument: str,
        cancel_oids: List[str],
        new_orders: List[Dict],
    ) -> List[Optional[HLFill]]:
        """Atomically cancel stale orders and place new orders in one PTB."""
        if not cancel_oids and not new_orders:
            return []

        try:
            tif = str(new_orders[0].get("tif", "PostOnly")) if new_orders else "PostOnly"
            order_type = _order_type_from_tif(tif)
            if order_type == 2 and new_orders:
                self._ensure_collateral_allocated(instrument, tif)

            acc_num = self._account_number()
            mkt = _get_market(self._base_url, instrument)
            ch_id = mkt["chId"]

            cancel_ids = [
                f"{str(oid).rstrip('n')}n"
                for oid in cancel_oids
                if str(oid).rstrip("n").isdigit()
            ]

            orders_to_place: List[Dict[str, Any]] = []
            for order in new_orders:
                side_raw = str(order.get("side", "buy")).lower()
                native_side = 0 if side_raw in ("buy", "bid", "long") else 1
                orders_to_place.append(
                    {
                        "side": native_side,
                        "price": _to_native_int(float(order.get("price", 0))),
                        "size": _to_native_int(float(order.get("size", 0))),
                    }
                )

            with _write_lock:
                has_pos = _has_position(self._base_url, acc_num, instrument)
                body: Dict[str, Any] = {
                    "accountId": f"{acc_num}n",
                    "walletAddress": self._wallet_address,
                    "marketId": ch_id,
                    "orderIdsToCancel": cancel_ids,
                    "ordersToPlace": orders_to_place,
                    "orderType": order_type,
                    "reduceOnly": False,
                    "hasPosition": has_pos,
                    "leverage": float(self._leverage),
                }
                _add_sponsor_to_body(body)

                resp = _request_with_retry(
                    "POST",
                    f"{self._base_url}/api/perpetuals/account/transactions/cancel-and-place-orders",
                    json=body,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    raise RuntimeError(f"AF cancel-and-place-orders error: {data['error']}")
                tx_kind_b64 = data.get("txKind")
                if not tx_kind_b64:
                    return [None for _ in new_orders]
                digest = _sign_and_submit(
                    self._private_key, tx_kind_b64, self._base_url, self._rpc_url,
                    sponsor_signature=data.get("sponsorSignature"),
                )
                _invalidate_position_cache()
                time.sleep(_settle_ms() / 1000.0)

            ts = int(time.time() * 1000)
            fills: List[Optional[HLFill]] = []
            for order in new_orders:
                order_side = str(order.get("side", "buy")).lower()
                hl_side = "buy" if order_side in ("buy", "bid", "long") else "sell"
                fills.append(
                    HLFill(
                        oid=digest,
                        instrument=_normalise_instrument(instrument),
                        side=hl_side,
                        price=Decimal(str(order.get("price", 0))),
                        quantity=Decimal(str(order.get("size", 0))),
                        timestamp_ms=ts,
                    )
                )

            log.info(
                "AF cancel_and_place_orders: cancelled=%d placed=%d %s -> %s",
                len(cancel_ids),
                len(new_orders),
                instrument,
                digest,
            )
            return fills
        except Exception as e:
            log.error("cancel_and_place_orders failed for %s: %s", instrument, e)
            return [None for _ in new_orders]

    def cancel_order(self, instrument: str, oid: str) -> bool:
        """Cancel a single order by OID."""
        try:
            acc_num = self._account_number()
            with _write_lock:
                digest = _cancel_orders_native(
                    base_url=self._base_url,
                    private_key=self._private_key,
                    rpc_url=self._rpc_url,
                    wallet_address=self._wallet_address,
                    account_number=acc_num,
                    instrument=instrument,
                    order_ids=[oid],
                )
                _invalidate_position_cache()
                time.sleep(_settle_ms() / 1000.0)
            log.info("AF cancelled order %s for %s -> %s", oid, instrument, digest)
            return True
        except Exception as e:
            log.error("cancel_order failed: %s / %s: %s", instrument, oid, e)
            return False

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        """Get open orders, optionally filtered by instrument."""
        try:
            acc_num = self._account_number()
            if instrument:
                return _get_open_orders(self._base_url, acc_num, instrument)
            # Fetch across all known markets
            all_orders: List[Dict] = []
            for base in list(_markets(self._base_url).keys()):
                try:
                    orders = _get_open_orders(
                        self._base_url, acc_num, f"{base}-AF-PERP"
                    )
                    all_orders.extend(orders)
                except Exception:
                    pass
            return all_orders
        except Exception as e:
            log.error("get_open_orders failed: %s", e)
            return []

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        """Fetch candle history (used by Radar/Pulse)."""
        instrument = f"{coin.upper()}-AF-PERP"
        return _get_candles(self._base_url, instrument, interval, lookback_ms)

    def get_all_markets(self) -> Any:
        """Return all markets in HL meta+assetCtxs format (used by Radar/Pulse)."""
        return _get_all_markets_hl_format(self._base_url)

    def get_all_mids(self) -> Dict[str, str]:
        """Return mid prices keyed by base asset (used by Radar/Pulse)."""
        return _get_all_mids(self._base_url)

    # -- Trigger orders (Guard exchange-level stop-loss sync) --

    def place_trigger_order(
        self,
        instrument: str,
        side: str,
        size: float,
        trigger_price: float,
    ) -> Optional[str]:
        """Place an on-chain stop-loss trigger order.

        Maps to Aftermath's place-stop-orders endpoint.
        Returns the stop-order Sui object ID (from stop-order-datas lookup
        after placement) or the tx digest as fallback.

        Body shape per OpenAPI spec:
          accountId: numeric
          walletAddress: string
          stopOrders: [{ marketId, size, side, nonSlTp: { stopIndexPrice, triggerIfGeStopIndexPrice, reduceOnly }}]
        """
        try:
            acc_num = self._account_number()
            mkt = _get_market(self._base_url, instrument)
            ch_id = mkt["chId"]
            native_side = 0 if side.lower() == "buy" else 1

            # For a stop-loss: trigger when index price crosses trigger_price.
            # If we're long (closing via sell), trigger when price drops BELOW -> triggerIfGe=False
            # If we're short (closing via buy), trigger when price rises ABOVE -> triggerIfGe=True
            trigger_if_ge = native_side == 0  # buy-to-close triggers on price >= threshold

            stop_order = {
                "marketId": ch_id,
                "size": int(round(size * 1e9)),  # native int, no "n" suffix for this endpoint
                "side": native_side,
                "nonSlTp": {
                    "stopIndexPrice": trigger_price,
                    "triggerIfGeStopIndexPrice": trigger_if_ge,
                    "reduceOnly": True,
                },
            }

            body = {
                "accountId": acc_num,
                "walletAddress": self._wallet_address,
                "stopOrders": [stop_order],
            }
            resp = _request_with_retry(
                "POST",
                f"{self._base_url}/api/perpetuals/account/transactions/place-stop-orders",
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                log.warning("place_trigger_order error: %s", data["error"])
                return None
            tx_kind_b64 = data.get("txKind")
            if not tx_kind_b64:
                return None
            with _write_lock:
                digest = _sign_and_submit(
                    self._private_key, tx_kind_b64, self._base_url, self._rpc_url
                )
                time.sleep(_settle_ms() / 1000.0)

            log.info("AF trigger SL placed: %s %s @ %.4f -> %s", side, size, trigger_price, digest)

            # Try to fetch the stop-order object ID for future cancellation.
            # stop-order-datas requires signed auth which we don't have here,
            # so return the tx digest as a fallback identifier.
            # Guard will use this OID for cancel_trigger_order.
            return digest
        except Exception as e:
            log.warning("place_trigger_order failed for %s: %s", instrument, e)
            return None

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        """Cancel a stop/trigger order by its Sui object ID.

        The cancel-stop-orders endpoint requires stopOrderIds (Sui object IDs),
        NOT tx digests. If the OID is a tx digest (0x... with 64 hex chars),
        this is a best-effort attempt — the endpoint may reject it.

        Body shape per OpenAPI spec:
          accountId: numeric
          walletAddress: string
          stopOrderIds: [string]  (Sui object IDs)
        """
        try:
            acc_num = self._account_number()

            body = {
                "accountId": acc_num,
                "walletAddress": self._wallet_address,
                "stopOrderIds": [oid],
            }
            resp = _request_with_retry(
                "POST",
                f"{self._base_url}/api/perpetuals/account/transactions/cancel-stop-orders",
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("error"):
                log.warning("cancel_trigger_order error: %s", data["error"])
                return False
            tx_kind_b64 = data.get("txKind")
            if tx_kind_b64:
                with _write_lock:
                    _sign_and_submit(
                        self._private_key, tx_kind_b64, self._base_url, self._rpc_url
                    )
                    time.sleep(_settle_ms() / 1000.0)
            return True
        except Exception as e:
            log.warning("cancel_trigger_order failed: %s", e)
            return False


# ---------------------------------------------------------------------------
# Mock proxy (testing / dry-run)
# ---------------------------------------------------------------------------


class AftermathMockProxy:
    """Mock Aftermath proxy for local testing — no real transactions."""

    def __init__(self, base_price: float = 2500.0, spread_bps: float = 2.0):
        self._base_price = base_price
        self._spread_bps = spread_bps
        self._tick = 0

    def get_snapshot(self, instrument: str = "ETH-AF-PERP") -> MarketSnapshot:
        import random
        drift = random.uniform(-5, 5)
        mid = self._base_price + drift
        half = mid * (self._spread_bps / 10_000 / 2)
        self._tick += 1
        return MarketSnapshot(
            instrument=_normalise_instrument(instrument),
            mid_price=round(mid, 4),
            bid=round(mid - half, 4),
            ask=round(mid + half, 4),
            spread_bps=round(self._spread_bps, 2),
            timestamp_ms=int(time.time() * 1000),
            volume_24h=1_000_000.0,
            funding_rate=0.0001,
            open_interest=500_000.0,
        )

    def get_account_state(self) -> Dict:
        return {
            "marginSummary": {"accountValue": 100_000.0},
            "withdrawable": 100_000.0,
            "address": "0xMOCK_AF",
        }

    def set_leverage(self, leverage: int, coin: str = "ETH", is_cross: bool = True) -> None:
        log.info("[MOCK-AF] set_leverage(%d, %s)", leverage, coin)

    def place_order(
        self,
        instrument: str,
        side: str,
        size: float,
        price: float,
        tif: str = "Ioc",
        builder: Optional[Dict] = None,
    ) -> Optional[HLFill]:
        oid = f"mock-af-{int(time.time() * 1000)}"
        log.info("[MOCK-AF] Filled [%s]: %s %s %s @ %s", tif, side, size, instrument, price)
        return HLFill(
            oid=oid,
            instrument=_normalise_instrument(instrument),
            side=side.lower(),
            price=Decimal(str(price)),
            quantity=Decimal(str(size)),
            timestamp_ms=int(time.time() * 1000),
        )

    def cancel_order(self, instrument: str, oid: str) -> bool:
        return True

    def cancel_and_place_orders(
        self,
        instrument: str,
        cancel_oids: List[str],
        new_orders: List[Dict],
    ) -> List[Optional[HLFill]]:
        log.info(
            "[MOCK-AF] cancel_and_place_orders(%s): cancel=%d place=%d",
            instrument,
            len(cancel_oids),
            len(new_orders),
        )
        ts = int(time.time() * 1000)
        fills: List[Optional[HLFill]] = []
        for order in new_orders:
            side = str(order.get("side", "buy")).lower()
            hl_side = "buy" if side in ("buy", "bid", "long") else "sell"
            fills.append(
                HLFill(
                    oid=f"mock-af-batch-{ts}",
                    instrument=_normalise_instrument(instrument),
                    side=hl_side,
                    price=Decimal(str(order.get("price", 0))),
                    quantity=Decimal(str(order.get("size", 0))),
                    timestamp_ms=ts,
                )
            )
        return fills

    def get_open_orders(self, instrument: str = "") -> List[Dict]:
        return []

    def get_candles(self, coin: str, interval: str, lookback_ms: int) -> List[Dict]:
        return []

    def get_all_markets(self) -> Any:
        return [{"universe": []}, []]

    def get_all_mids(self) -> Dict[str, str]:
        return {}

    def place_trigger_order(
        self, instrument: str, side: str, size: float, trigger_price: float
    ) -> Optional[str]:
        return f"mock-trig-{int(time.time() * 1000)}"

    def cancel_trigger_order(self, instrument: str, oid: str) -> bool:
        return True
