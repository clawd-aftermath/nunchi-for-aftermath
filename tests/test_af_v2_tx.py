"""The transaction gate: preview unions, inspection, and the arming boundary.

The central claim under test is that there is no path from a builder response
to a signature that skips inspection. If that claim ever stops holding, these
tests fail.
"""
from __future__ import annotations

import base64

import pytest

from cli.af.api import PreviewErr, PreviewOk, is_preview_error_body
from cli.af.gas import GasConfig
from cli.af.tx import (
    InspectedTx,
    NotArmedError,
    TxExpectation,
    TxInspectionError,
    inspect,
    reconcile,
    sign_inspected,
)

SENDER = "0x" + "11" * 32
SPONSOR = "0x" + "22" * 32
GOOD_KIND = base64.b64encode(b"a-transaction-kind").decode()


def _expect(gas: GasConfig, **kw) -> TxExpectation:
    return TxExpectation(sender=SENDER, gas=gas, intent="test order", **kw)


# ── Preview tagged unions (gotcha 6) ─────────────────────────────


def test_preview_error_body_is_detected():
    """A preview can return HTTP 200 with an error body."""
    assert is_preview_error_body({"error": "insufficient margin"})
    assert not is_preview_error_body({"estimatedFee": 1})
    assert not is_preview_error_body(None)


def test_preview_results_are_a_tagged_union():
    assert PreviewOk({"a": 1}).ok is True
    assert PreviewErr("nope").ok is False


def test_preview_fails_closed_on_transport_error(monkeypatch):
    """A preview that cannot be evaluated is never permission to transact."""
    from cli.af import api

    def boom(*a, **k):
        raise api.AfApiError("connection reset")

    monkeypatch.setattr(api, "post", boom)
    result = api.preview("/api/perpetuals/account/previews/place-limit-order", {})
    assert result.ok is False
    assert "connection reset" in result.error


def test_preview_200_with_error_body_is_a_failure(monkeypatch):
    from cli.af import api

    monkeypatch.setattr(api, "post", lambda *a, **k: {"error": "market closed"})
    result = api.preview("/any/preview", {})
    assert result.ok is False and result.error == "market closed"


# ── Inspection ───────────────────────────────────────────────────


def test_inspect_accepts_a_well_formed_unsponsored_build():
    tx = inspect({"txKind": GOOD_KIND}, _expect(GasConfig(mode="self")))
    assert isinstance(tx, InspectedTx)
    assert tx.is_sponsored is False


def test_inspect_accepts_a_well_formed_sponsored_build():
    gas = GasConfig(mode="sponsored", sponsor=SPONSOR)
    tx = inspect({"txKind": GOOD_KIND, "sponsorSignature": "sig"}, _expect(gas))
    assert tx.is_sponsored is True


def test_inspect_rejects_a_missing_tx_kind():
    with pytest.raises(TxInspectionError, match="no txKind"):
        inspect({"sponsorSignature": "sig"}, _expect(GasConfig(mode="self")))


def test_inspect_rejects_non_base64():
    with pytest.raises(TxInspectionError, match="not valid base64"):
        inspect({"txKind": "!!!not base64!!!"}, _expect(GasConfig(mode="self")))


def test_inspect_rejects_a_sender_mismatch():
    other = "0x" + "99" * 32
    with pytest.raises(TxInspectionError, match="sender mismatch"):
        inspect({"txKind": GOOD_KIND, "sender": other}, _expect(GasConfig(mode="self")))


def test_inspect_rejects_a_package_mismatch():
    exp = _expect(GasConfig(mode="self"), expected_package="0xaaa")
    with pytest.raises(TxInspectionError, match="package mismatch"):
        inspect({"txKind": GOOD_KIND, "packageId": "0xbbb"}, exp)


def test_self_gas_must_not_come_back_sponsored():
    """Someone else paying when the operator chose 'self' is a real finding."""
    with pytest.raises(TxInspectionError, match="gas mode is 'self'"):
        inspect({"txKind": GOOD_KIND, "sponsorSignature": "sig"}, _expect(GasConfig(mode="self")))


def test_sponsored_gas_must_actually_come_back_sponsored():
    gas = GasConfig(mode="sponsored", sponsor=SPONSOR)
    with pytest.raises(TxInspectionError, match="no sponsor signature"):
        inspect({"txKind": GOOD_KIND}, _expect(gas))


def test_sponsor_may_equal_sender():
    """On Sui the sponsor and sender may legitimately be the same address."""
    gas = GasConfig(mode="sponsored", sponsor=SENDER)
    tx = inspect({"txKind": GOOD_KIND, "sponsorSignature": "sig", "sender": SENDER}, _expect(gas))
    assert tx.is_sponsored


def test_inspect_rejects_a_non_object_response():
    with pytest.raises(TxInspectionError):
        inspect("not a dict", _expect(GasConfig(mode="self")))


# ── The gate is not bypassable ───────────────────────────────────


def test_inspected_tx_cannot_be_forged():
    """The only way to obtain an InspectedTx is through inspect()."""
    with pytest.raises(TxInspectionError, match="cannot be bypassed"):
        InspectedTx(
            tx_kind=GOOD_KIND,
            sponsor_signature=None,
            expectation=_expect(GasConfig(mode="self")),
        )


def test_sign_requires_an_inspected_tx():
    with pytest.raises(TxInspectionError):
        sign_inspected({"txKind": GOOD_KIND}, [lambda d: "sig"], armed=True)


def test_sign_refuses_while_disarmed():
    tx = inspect({"txKind": GOOD_KIND}, _expect(GasConfig(mode="self")))
    with pytest.raises(NotArmedError, match="disarmed"):
        sign_inspected(tx, [lambda d: "sig"], armed=False)


def test_sign_returns_a_list_because_signatures_are_plural():
    """gotcha 14: /api/ccxt/submit/* takes `signatures[]`.

    When sender and gas owner differ, both sign the SAME digest.
    """
    tx = inspect({"txKind": GOOD_KIND}, _expect(GasConfig(mode="self")))
    sigs = sign_inspected(tx, [lambda d: f"sig-a:{d[:4]}", lambda d: f"sig-b:{d[:4]}"], armed=True)
    assert isinstance(sigs, list) and len(sigs) == 2
    assert sigs[0] != sigs[1]


# ── Build gating ─────────────────────────────────────────────────


def test_build_is_blocked_when_the_preview_rejects(monkeypatch):
    """A preview error blocks the build entirely -- nothing is constructed."""
    from cli.af import api, tx as txmod

    built = []
    monkeypatch.setattr(api, "preview", lambda p, b, **k: PreviewErr("insufficient margin"))
    monkeypatch.setattr(api, "post", lambda *a, **k: built.append(a) or {"txKind": GOOD_KIND})

    with pytest.raises(TxInspectionError, match="preview rejected"):
        txmod.build_gated(
            preview_path="/preview",
            build_path="/build",
            body={},
            gas=GasConfig(mode="self"),
            expectation=_expect(GasConfig(mode="self")),
        )
    assert built == [], "no transaction may be built after a failed preview"


def test_build_proceeds_when_the_preview_passes(monkeypatch):
    from cli.af import api, tx as txmod

    monkeypatch.setattr(api, "preview", lambda p, b, **k: PreviewOk({"fee": 1}))
    monkeypatch.setattr(api, "post", lambda *a, **k: {"txKind": GOOD_KIND})

    result = txmod.build_gated(
        preview_path="/preview",
        build_path="/build",
        body={},
        gas=GasConfig(mode="self"),
        expectation=_expect(GasConfig(mode="self")),
    )
    assert isinstance(result, InspectedTx)


def test_gas_is_applied_to_the_built_body(monkeypatch):
    from cli.af import api, tx as txmod

    seen = {}
    monkeypatch.setattr(
        api,
        "post",
        lambda path, body, **k: seen.update(body) or {"txKind": GOOD_KIND, "sponsorSignature": "sig"},
    )

    txmod.build_gated(
        build_path="/build",
        body={"marketId": "0x1"},
        gas=GasConfig(mode="sponsored", sponsor=SPONSOR, budget_mist=777),
        expectation=_expect(GasConfig(mode="sponsored", sponsor=SPONSOR)),
    )
    assert seen["gasBudget"] == "777"
    assert seen["sponsor"] == {"walletAddress": SPONSOR}


# ── Reconciliation ───────────────────────────────────────────────


def test_reconcile_succeeds_once_state_is_observed():
    calls = {"n": 0}

    def verify():
        calls["n"] += 1
        return calls["n"] >= 2

    reconcile(verify, attempts=4, delay_s=0.0, intent="test")
    assert calls["n"] == 2


def test_reconcile_raises_when_state_never_matches():
    """Submit returning 200 means 'accepted', never 'applied'."""
    with pytest.raises(RuntimeError, match="reconciliation failed"):
        reconcile(lambda: False, attempts=2, delay_s=0.0, intent="test")


# ── The adapter's submit boundary ────────────────────────────────


def test_adapter_never_submits_while_disarmed():
    """This build ships disarmed and without a signer, by design."""
    from cli.af.config import AfConfig
    from cli.af.proxy import AftermathProxy

    proxy = AftermathProxy(AfConfig(wallet_address=SENDER, armed=False))
    tx = inspect({"txKind": GOOD_KIND}, _expect(GasConfig(mode="self")))
    with pytest.raises(NotArmedError, match="did not submit"):
        proxy._submit(tx)


def test_adapter_still_refuses_when_armed_without_a_signer():
    from cli.af.config import AfConfig
    from cli.af.proxy import AftermathProxy

    proxy = AftermathProxy(AfConfig(wallet_address=SENDER, armed=True))
    tx = inspect({"txKind": GOOD_KIND}, _expect(GasConfig(mode="self")))
    with pytest.raises(NotArmedError, match="signer=absent"):
        proxy._submit(tx)


# ── v3.0.0 renames ───────────────────────────────────────────────


def test_builder_code_uses_v3_field_names():
    """v3.0.0: integratorId (u32 number) + integratorFee, not address + takerFee."""
    from cli.af.proxy import _normalise_builder_code

    assert _normalise_builder_code({"integratorId": 7, "integratorFee": 0.001}) == {
        "integratorId": 7,
        "integratorFee": 0.001,
    }
    # A pre-v3 payload with a numeric id is translated rather than sent as-is.
    assert _normalise_builder_code({"integratorId": 7, "takerFee": 0.002}) == {
        "integratorId": 7,
        "integratorFee": 0.002,
    }


def test_builder_code_rejects_the_removed_address_form():
    """`integratorAddress` as an address string is no longer valid in v3.0.0."""
    from cli.af.proxy import _normalise_builder_code

    with pytest.raises(ValueError, match="u32 number"):
        _normalise_builder_code({"integratorAddress": "0xdeadbeef", "takerFee": 0.001})


def test_candle_resolution_replaces_interval_ms():
    """v3.0.0 renamed candle `intervalMs`/`interval_ms` to `resolution`."""
    from cli.af.proxy import _resolution

    assert _resolution("1m") == "1m"
    assert _resolution("1h") == "1h"
    assert _resolution("60000") == "1m"  # legacy ms converted, never sent raw
