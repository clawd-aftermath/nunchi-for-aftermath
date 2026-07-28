"""Regression tests for the post-relaunch Aftermath Perpetuals API."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cli import af_proxy

MARKET_ID = "0x" + "1" * 64


class _Response:
    def __init__(self, data, *, status_code: int = 200, headers=None):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_v2_preview_is_the_default_api_base():
    assert af_proxy.AF_BASE_URL_DEFAULT == "https://v2-preview.aftermath.finance"


def test_fetch_markets_accepts_v2_ccxt_array_response():
    response = _Response(
        [
            {
                "id": MARKET_ID,
                "symbol": "BTC/USD:USDC",
                "base": "BTC",
                "active": True,
                "swap": True,
                "precision": {"price": 0.0001},
                "limits": {"cost": {"min": 1.0}},
            }
        ]
    )

    with patch.object(af_proxy, "_request_with_retry", return_value=response):
        markets = af_proxy._fetch_markets("https://example.test")

    assert markets["BTC"]["chId"] == MARKET_ID
    assert markets["BTC"]["tickSize"] == 0.0001
    assert markets["BTC"]["minSize"] == 0.001


def test_fetch_all_markets_unwraps_v2_response_and_flattens_market_state():
    response = _Response(
        {
            "markets": [
                {
                    "objectId": MARKET_ID,
                    "indexPrice": 64_000,
                    "estimatedFundingRate": -0.000025,
                    "marketState": {"openInterest": 12.5, "premiumTwap": -1.2},
                }
            ]
        }
    )

    with patch.object(af_proxy, "_request_with_retry", return_value=response):
        markets = af_proxy._fetch_all_markets("https://example.test")

    assert markets[MARKET_ID]["indexPrice"] == 64_000
    assert markets[MARKET_ID]["openInterest"] == 12.5
    assert markets[MARKET_ID]["estimatedFundingRate"] == -0.000025


def test_account_discovery_accepts_v2_object_id():
    responses = [
        _Response([]),
        _Response(
            {
                "accountCaps": [
                    {
                        "objectId": "0xaccount-cap",
                        "accountId": "7n",
                    }
                ]
            }
        ),
    ]

    with patch.object(af_proxy, "_request_with_retry", side_effect=responses):
        account = af_proxy._fetch_account_info(
            "https://example.test",
            "0xwallet",
        )

    assert account == {
        "accountNumber": 7,
        "accountCapId": "0xaccount-cap",
        "walletAddress": "0xwallet",
    }


def test_fetch_snapshot_parses_v2_orderbook_and_stats_wrappers():
    responses = [
        _Response(
            {
                "orderbooks": [
                    {
                        "marketId": MARKET_ID,
                        "orderbook": {
                            "bids": [
                                {"price": 100.0, "size": 1.0},
                                {"price": 99.0, "size": 2.0},
                            ],
                            "asks": [
                                {"price": 0, "size": 99.0},
                                {"price": 100.5, "size": 0},
                                {"unexpected": "level"},
                                {"price": 101.0, "size": 1.0},
                                {"price": 102.0, "size": 2.0},
                            ],
                        },
                    }
                ]
            }
        ),
        _Response(
            {
                "marketsStats": [
                    {
                        "volumeUsd": 1234.5,
                        "markPrice": 100.5,
                    }
                ]
            }
        ),
    ]

    with (
        patch.object(
            af_proxy,
            "_get_market",
            return_value={"chId": MARKET_ID, "base": "BTC"},
        ),
        patch.object(af_proxy, "_request_with_retry", side_effect=responses),
        patch.object(
            af_proxy,
            "_all_markets",
            return_value={
                MARKET_ID: {
                    "estimatedFundingRate": 0.0001,
                    "openInterest": 42.0,
                }
            },
        ),
    ):
        snapshot = af_proxy._fetch_snapshot("https://example.test", "BTC-AF-PERP")

    assert snapshot.bid == 100.0
    assert snapshot.ask == 101.0
    assert snapshot.mid_price == 100.5
    assert snapshot.volume_24h == 1234.5
    assert snapshot.open_interest == 42.0
    assert snapshot.funding_rate == 0.0001


def test_fetch_snapshot_rejects_crossed_v2_orderbook():
    response = _Response(
        {
            "orderbooks": [
                {
                    "marketId": MARKET_ID,
                    "orderbook": {
                        "bids": [{"price": 102.0, "size": 1.0}],
                        "asks": [{"price": 101.0, "size": 1.0}],
                    },
                }
            ]
        }
    )

    with (
        patch.object(
            af_proxy,
            "_get_market",
            return_value={"chId": MARKET_ID, "base": "BTC"},
        ),
        patch.object(af_proxy, "_request_with_retry", return_value=response),
        pytest.raises(RuntimeError, match="orderbook is crossed"),
    ):
        af_proxy._fetch_snapshot("https://example.test", "BTC-AF-PERP")


def test_candle_history_uses_v2_request_fields():
    response = _Response(
        {
            "candles": [
                {
                    "timestamp": 123,
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 9.0,
                }
            ]
        }
    )

    with (
        patch.object(
            af_proxy,
            "_get_market",
            return_value={"chId": MARKET_ID, "base": "BTC"},
        ),
        patch.object(
            af_proxy,
            "_request_with_retry",
            return_value=response,
        ) as request,
        patch.object(af_proxy.time, "time", return_value=1000),
    ):
        candles = af_proxy._get_candles(
            "https://example.test",
            "BTC-AF-PERP",
            "5m",
            60_000,
        )

    payload = request.call_args.kwargs["json"]
    assert payload == {
        "marketId": MARKET_ID,
        "resolution": "5m",
        "fromTimestamp": 940_000,
        "toTimestamp": 1_000_000,
    }
    assert candles[0] == {
        "t": 123,
        "o": "1.0",
        "h": "2.0",
        "l": "0.5",
        "c": "1.5",
        "v": "9.0",
    }


def test_get_all_mids_unwraps_v2_prices_response():
    response = _Response(
        {
            "marketsPrices": [
                {
                    "marketId": MARKET_ID,
                    "markPrice": 64_123.5,
                    "basePrice": 64_100.0,
                }
            ]
        }
    )

    with (
        patch.object(
            af_proxy,
            "_markets",
            return_value={"BTC": {"chId": MARKET_ID, "base": "BTC"}},
        ),
        patch.object(af_proxy, "_request_with_retry", return_value=response),
    ):
        mids = af_proxy._get_all_mids("https://example.test")

    assert mids == {"BTC": "64123.5"}


def test_hl_market_context_drops_misaligned_v2_stats_rows():
    markets = {
        "BTC": {"chId": MARKET_ID, "base": "BTC"},
        "ETH": {"chId": "0x" + "2" * 64, "base": "ETH"},
    }
    response = _Response({"marketsStats": [{"volumeUsd": 999.0}]})

    with (
        patch.object(af_proxy, "_markets", return_value=markets),
        patch.object(af_proxy, "_all_markets", return_value={}),
        patch.object(af_proxy, "_request_with_retry", return_value=response),
    ):
        _, contexts = af_proxy._get_all_markets_hl_format("https://example.test")

    assert [context["dayNtlVlm"] for context in contexts] == ["0", "0"]


def test_hl_market_context_keys_v2_stats_by_explicit_market_id():
    eth_market_id = "0x" + "2" * 64
    markets = {
        "BTC": {"chId": MARKET_ID, "base": "BTC"},
        "ETH": {"chId": eth_market_id, "base": "ETH"},
    }
    response = _Response(
        {
            "marketsStats": [
                {"marketId": eth_market_id, "volumeUsd": 2.0},
                {"marketId": MARKET_ID, "volumeUsd": 1.0},
            ]
        }
    )

    with (
        patch.object(af_proxy, "_markets", return_value=markets),
        patch.object(af_proxy, "_all_markets", return_value={}),
        patch.object(af_proxy, "_request_with_retry", return_value=response),
    ):
        _, contexts = af_proxy._get_all_markets_hl_format("https://example.test")

    assert [context["dayNtlVlm"] for context in contexts] == ["1.0", "2.0"]


def test_allocate_collateral_uses_native_units_and_forwards_sponsor_signature():
    proxy = af_proxy.AftermathProxy.__new__(af_proxy.AftermathProxy)
    proxy._base_url = "https://example.test"
    proxy._wallet_address = "0xsender"
    proxy._private_key = "private"
    proxy._rpc_url = "https://rpc.test"
    proxy._collateral_allocated = set()

    response = _Response(
        {
            "txKind": "dHg=",
            "sponsorSignature": "sponsor-signature",
        }
    )

    with (
        patch.object(proxy, "_account_number", return_value=7),
        patch.object(
            af_proxy,
            "_get_market",
            return_value={"chId": MARKET_ID, "base": "BTC"},
        ),
        patch.object(af_proxy, "_sponsor_address", return_value="0xsponsor"),
        patch.object(
            af_proxy,
            "_request_with_retry",
            return_value=response,
        ) as request,
        patch.object(
            af_proxy,
            "_sign_and_submit",
            return_value="digest",
        ) as submit,
        patch.object(af_proxy.time, "sleep"),
    ):
        digest = proxy.allocate_collateral("BTC-AF-PERP", 100.0)

    assert digest == "digest"
    assert request.call_args.kwargs["json"] == {
        "accountId": "7n",
        "walletAddress": "0xsender",
        "marketId": MARKET_ID,
        "allocateAmount": "100000000n",
        "sponsor": {"walletAddress": "0xsponsor"},
    }
    assert submit.call_args.kwargs["sponsor_signature"] == "sponsor-signature"


def test_collateral_allocation_rejects_subnative_positive_amount(monkeypatch):
    monkeypatch.setenv("AF_COLLATERAL_DECIMALS", "6")

    with pytest.raises(ValueError, match="below 6-decimal precision"):
        af_proxy._to_collateral_int(0.0000001)


def test_cancel_and_place_orders_includes_v2_transaction_flags():
    proxy = af_proxy.AftermathProxy.__new__(af_proxy.AftermathProxy)
    proxy._base_url = "https://example.test"
    proxy._wallet_address = "0xsender"
    proxy._private_key = "private"
    proxy._rpc_url = "https://rpc.test"
    proxy._leverage = 3

    response = _Response({"txKind": "dHg="})

    with (
        patch.object(proxy, "_ensure_collateral_allocated"),
        patch.object(proxy, "_account_number", return_value=7),
        patch.object(
            af_proxy,
            "_get_market",
            return_value={"chId": MARKET_ID, "base": "BTC"},
        ),
        patch.object(af_proxy, "_has_position", return_value=False),
        patch.object(af_proxy, "_sponsor_address", return_value=None),
        patch.object(
            af_proxy,
            "_request_with_retry",
            return_value=response,
        ) as request,
        patch.object(af_proxy, "_sign_and_submit", return_value="digest"),
        patch.object(af_proxy.time, "sleep"),
    ):
        fills = proxy.cancel_and_place_orders(
            "BTC-AF-PERP",
            ["123"],
            [
                {
                    "side": "buy",
                    "price": 64_000,
                    "size": 0.01,
                    "tif": "PostOnly",
                }
            ],
        )

    payload = request.call_args.kwargs["json"]
    assert payload["shouldAbortOnMissingId"] is False
    assert payload["shouldDeallocateFreeCollateral"] is False
    assert payload["orderIdsToCancel"] == ["123n"]
    assert fills[0].oid == "digest"


def test_node_signer_payload_includes_sponsor_signature():
    completed = MagicMock(returncode=0, stdout='{"digest":"digest"}', stderr="")

    with (
        patch.object(
            af_proxy,
            "_get_node_signer_script",
            return_value="/tmp/signer.mjs",
        ),
        patch("subprocess.run", return_value=completed) as run,
    ):
        digest = af_proxy._node_sign_submit(
            "private",
            "dHg=",
            "https://rpc.test",
            sponsor_signature="sponsor-signature",
        )

    assert digest == "digest"
    payload = json.loads(run.call_args.kwargs["input"])
    assert payload["sponsorSignature"] == "sponsor-signature"


def test_preview_error_header_is_not_treated_as_success():
    response = _Response(
        {"message": "preview failed"},
        headers={"X-Error-Message": "true"},
    )

    with (
        patch.object(af_proxy, "_request_with_retry", return_value=response),
        pytest.raises(RuntimeError, match="Aftermath preview failed"),
    ):
        af_proxy._preview_limit_order(
            "https://example.test",
            1,
            MARKET_ID,
            0,
            0.01,
            64_000,
            0,
        )
