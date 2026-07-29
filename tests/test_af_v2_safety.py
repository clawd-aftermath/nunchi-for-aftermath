"""Safety conformance: margin zones, sizing, circuit breakers, kill switch, tx gate.

These behaviours are required by the vendored `safety-and-risk.md` (v3.0.0) and
live in the adapter so they apply to every strategy at once. The tests exist to
prove they cannot be bypassed.
"""
from __future__ import annotations

import pytest

from cli.af.safety import (
    DANGER,
    LIQUIDATION,
    SAFE,
    WARNING,
    BotState,
    CircuitBreaker,
    HardLimits,
    KillSwitch,
    SoftLimits,
    assess_margin_health,
    check_soft_limits,
    enforce_hard_limits,
    max_size_for_risk,
)


# ── Margin health zones ──────────────────────────────────────────


@pytest.mark.parametrize(
    "ratio,maintenance,zone",
    [
        (0.20, 0.05, SAFE),        # 4.0x buffer
        (0.11, 0.05, SAFE),        # 2.2x
        (0.09, 0.05, WARNING),     # 1.8x
        (0.06, 0.05, DANGER),      # 1.2x
        (0.04, 0.05, LIQUIDATION), # 0.8x
    ],
)
def test_margin_zones(ratio, maintenance, zone):
    assert assess_margin_health(ratio, maintenance).zone == zone


def test_margin_health_refuses_to_guess_on_missing_data():
    """Coercing an unknown margin ratio to a number invents a risk answer."""
    with pytest.raises(ValueError, match="refusing to guess"):
        assess_margin_health(None, 0.05)
    with pytest.raises(ValueError, match="refusing to guess"):
        assess_margin_health(float("nan"), 0.05)


def test_maintenance_ratio_must_be_positive():
    with pytest.raises(ValueError):
        assess_margin_health(0.1, 0.0)


# ── Position sizing ──────────────────────────────────────────────


def test_two_percent_rule():
    # 10,000 collateral, 2% risk = 200 at risk; stop 50 away -> 4 units.
    assert max_size_for_risk(10_000, 2_500, 2_450, 2.0) == pytest.approx(4.0)


def test_sizing_rejects_a_zero_stop_distance():
    with pytest.raises(ValueError, match="must differ"):
        max_size_for_risk(10_000, 2_500, 2_500)


def test_sizing_rejects_nonpositive_collateral():
    with pytest.raises(ValueError):
        max_size_for_risk(0, 2_500, 2_450)


# ── Two-tier circuit breakers ────────────────────────────────────


def test_soft_limits_warn_without_halting():
    warnings = check_soft_limits(
        SoftLimits(max_drawdown_pct=0.05, max_leverage=5),
        BotState(drawdown_pct=0.08, effective_leverage=9.0, margin_buffer=10.0),
    )
    assert len(warnings) == 2
    assert enforce_hard_limits(HardLimits(), BotState(drawdown_pct=0.08)) is None


def test_hard_limits_halt():
    assert "drawdown" in enforce_hard_limits(HardLimits(max_drawdown_pct=0.15), BotState(drawdown_pct=0.20))
    assert "daily loss" in enforce_hard_limits(HardLimits(max_daily_loss=100), BotState(daily_loss=200))
    assert "daily trade" in enforce_hard_limits(HardLimits(max_daily_trades=10), BotState(daily_trade_count=11))


def test_a_tripped_breaker_stays_tripped():
    """A breaker that re-evaluates itself back to 'fine' is not a breaker."""
    cb = CircuitBreaker(hard=HardLimits(max_daily_loss=100))
    assert cb.evaluate(BotState(daily_loss=500)) is not None
    assert cb.tripped
    # Even with a now-healthy state, it stays halted until reset.
    assert cb.evaluate(BotState(daily_loss=0)) is not None
    cb.reset()
    assert cb.evaluate(BotState(daily_loss=0)) is None


# ── Kill switch ──────────────────────────────────────────────────


def test_kill_switch_fires_on_heartbeat_timeout():
    fired = []
    ks = KillSwitch(0.0, cancel_all=lambda: fired.append(True), log_fn=lambda m: None)
    assert ks.check() is True
    assert fired == [True]
    assert ks.is_armed is False


def test_kill_switch_does_not_fire_while_the_loop_beats():
    fired = []
    ks = KillSwitch(60.0, cancel_all=lambda: fired.append(True), log_fn=lambda m: None)
    ks.heartbeat()
    assert ks.check() is False
    assert fired == []


def test_kill_switch_rearms_when_cancellation_fails():
    """Failed cancellation means the exposure is still live -- stay armed."""
    def boom():
        raise RuntimeError("cancel failed")

    ks = KillSwitch(0.0, cancel_all=boom, log_fn=lambda m: None)
    with pytest.raises(RuntimeError):
        ks.trigger("test")
    assert ks.is_armed is True, "a failed kill switch must remain armed to retry"


def test_kill_switch_fires_only_once_per_arming():
    calls = []
    ks = KillSwitch(0.0, cancel_all=lambda: calls.append(1), log_fn=lambda m: None)
    ks.trigger("first")
    ks.trigger("second")
    assert len(calls) == 1


def test_cancellation_is_verified_not_assumed():
    """The mock's cancel-all re-checks the book, mirroring the real adapter."""
    from cli.af.mock import AftermathMockProxy

    m = AftermathMockProxy(seed=3)
    snap = m.get_snapshot("ETH")
    m.place_order("ETH", "buy", 1.0, snap.bid, tif="Alo")
    m.place_order("ETH", "sell", 1.0, snap.ask, tif="Alo")
    assert len(m.get_open_orders()) == 2
    m.cancel_all_verified()
    assert m.get_open_orders() == []


def test_shutdown_sets_nonzero_exit_code_when_cancellation_fails():
    from cli.af.safety import ShutdownState, install_shutdown_handlers

    def boom():
        raise RuntimeError("cancel failed")

    ks = KillSwitch(0.0, cancel_all=boom, log_fn=lambda m: None)
    st = ShutdownState()
    exits = []
    install_shutdown_handlers(ks, timeout_s=2.0, state=st, exit_fn=exits.append)
    getattr(st, "_shutdown")("SIGTERM — test")
    assert st.shutting_down is True
    assert exits == [1], "a shutdown that left orders resting must exit non-zero"


def test_shutdown_exits_zero_on_a_clean_cancel():
    from cli.af.safety import ShutdownState, install_shutdown_handlers

    ks = KillSwitch(0.0, cancel_all=lambda: None, log_fn=lambda m: None)
    st = ShutdownState()
    exits = []
    install_shutdown_handlers(ks, timeout_s=2.0, state=st, exit_fn=exits.append)
    getattr(st, "_shutdown")("SIGINT — test")
    assert exits == [0]
