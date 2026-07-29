"""Trading safety: margin health, sizing, circuit breakers, kill switch.

Structure follows the official Aftermath skill ``safety-and-risk.md`` (v3.0.0),
vendored at ``AFTERMATH_SKILLS_REF/``. Only the skill's *patterns* are reused --
its example code targets the retired v1 host, while every request here goes
through :mod:`cli.af.api`, which resolves the host from the one config constant.

These live below the strategy layer deliberately. Putting them in the adapter
means they apply to every strategy at once and cannot be bypassed by one that
forgets to check.
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

log = logging.getLogger("af.safety")


# ── Margin health ────────────────────────────────────────────────
# Aftermath uses ISOLATED margin. Each position carries its own allocation and
# unallocated account collateral protects nothing. Health is therefore the
# position's own marginRatio measured against the market's maintenance ratio --
# NOT account equity over total notional, which is the cross-margin question
# and would read a doomed position as healthy.

SAFE = "SAFE"
WARNING = "WARNING"
DANGER = "DANGER"
LIQUIDATION = "LIQUIDATION"
NO_POSITION = "NO_POSITION"


@dataclass(frozen=True)
class MarginHealth:
    zone: str
    margin_ratio: float
    maintenance_ratio: float
    #: How far above maintenance, as a multiple. Below 1.0 is liquidatable.
    buffer_multiple: float


def assess_margin_health(margin_ratio: float, maintenance_ratio: float) -> MarginHealth:
    """Classify a position's margin health.

    Raises on missing or non-finite inputs rather than guessing: an
    unknown margin ratio silently coerced to zero reads as an imminent
    liquidation, and coerced to a large number reads as perfectly safe. Both
    are worse than an error.
    """
    for name, val in (("marginRatio", margin_ratio), ("marginRatioMaintenance", maintenance_ratio)):
        if val is None or not isinstance(val, (int, float)) or val != val or val in (float("inf"), float("-inf")):
            raise ValueError(f"{name} missing or non-finite — refusing to guess position health")
    if maintenance_ratio <= 0:
        raise ValueError("marginRatioMaintenance must be positive")

    buffer = float(margin_ratio) / float(maintenance_ratio)
    if buffer < 1.0:
        zone = LIQUIDATION
    elif buffer < 1.5:
        zone = DANGER
    elif buffer < 2.0:
        zone = WARNING
    else:
        zone = SAFE
    return MarginHealth(zone, float(margin_ratio), float(maintenance_ratio), buffer)


# ── Position sizing ──────────────────────────────────────────────


def max_size_for_risk(
    account_collateral: float,
    entry_price: float,
    stop_loss_price: float,
    risk_percent: float = 2.0,
) -> float:
    """Largest position that risks at most ``risk_percent`` of collateral.

    The 2% rule, computed off account collateral and the distance to the stop.
    """
    if account_collateral <= 0:
        raise ValueError("account collateral must be positive")
    if not 0 < risk_percent <= 100:
        raise ValueError("risk_percent must be in (0, 100]")
    distance = abs(float(entry_price) - float(stop_loss_price))
    if distance <= 0:
        raise ValueError("entry and stop-loss prices must differ")
    return (account_collateral * (risk_percent / 100.0)) / distance


# ── Circuit breakers ─────────────────────────────────────────────
# Two tiers, because "warn" and "stop" are different decisions. Soft limits
# surface a developing problem while there is still room to act; hard limits
# halt trading outright.


@dataclass
class SoftLimits:
    max_drawdown_pct: float = 0.05
    max_position_notional: float = 50_000.0
    max_leverage: float = 5.0
    min_margin_buffer: float = 2.0


@dataclass
class HardLimits:
    max_drawdown_pct: float = 0.15
    max_daily_loss: float = 5_000.0
    max_daily_trades: int = 200


@dataclass
class BotState:
    drawdown_pct: float = 0.0
    position_notional: float = 0.0
    effective_leverage: float = 0.0
    margin_buffer: float = float("inf")
    daily_loss: float = 0.0
    daily_trade_count: int = 0


def check_soft_limits(limits: SoftLimits, state: BotState) -> List[str]:
    """Tier 1 -- advisory. Returns warnings; does not stop trading."""
    warnings: List[str] = []
    if state.drawdown_pct > limits.max_drawdown_pct:
        warnings.append(
            f"drawdown {state.drawdown_pct * 100:.1f}% exceeds soft limit {limits.max_drawdown_pct * 100:.1f}%"
        )
    if state.position_notional > limits.max_position_notional:
        warnings.append(
            f"position notional {state.position_notional:.2f} exceeds {limits.max_position_notional:.2f}"
        )
    if state.effective_leverage > limits.max_leverage:
        warnings.append(f"leverage {state.effective_leverage:.1f}x exceeds {limits.max_leverage:.1f}x")
    if state.margin_buffer < limits.min_margin_buffer:
        warnings.append(f"margin buffer {state.margin_buffer:.2f}x below {limits.min_margin_buffer:.2f}x")
    return warnings


def enforce_hard_limits(limits: HardLimits, state: BotState) -> Optional[str]:
    """Tier 2 -- binding. A non-None return means STOP TRADING NOW."""
    if state.drawdown_pct > limits.max_drawdown_pct:
        return "HALT: maximum drawdown exceeded"
    if state.daily_loss > limits.max_daily_loss:
        return "HALT: daily loss limit reached"
    if state.daily_trade_count > limits.max_daily_trades:
        return "HALT: daily trade limit reached"
    return None


class CircuitBreaker:
    """Holds both tiers plus the tripped state, so a halt is sticky.

    Once tripped, the breaker stays tripped until explicitly reset. A breaker
    that re-evaluates itself back to "fine" on the next tick is not a breaker.
    """

    def __init__(self, soft: Optional[SoftLimits] = None, hard: Optional[HardLimits] = None):
        self.soft = soft or SoftLimits()
        self.hard = hard or HardLimits()
        self._tripped_reason: Optional[str] = None

    @property
    def tripped(self) -> bool:
        return self._tripped_reason is not None

    @property
    def reason(self) -> Optional[str]:
        return self._tripped_reason

    def evaluate(self, state: BotState) -> Optional[str]:
        """Check both tiers. Returns the halt reason if trading must stop."""
        if self._tripped_reason:
            return self._tripped_reason
        for w in check_soft_limits(self.soft, state):
            log.warning("circuit breaker (soft): %s", w)
        halt = enforce_hard_limits(self.hard, state)
        if halt:
            self._tripped_reason = halt
            log.error("circuit breaker (hard): %s", halt)
        return halt

    def trip(self, reason: str) -> None:
        self._tripped_reason = reason

    def reset(self) -> None:
        self._tripped_reason = None


# ── Kill switch ──────────────────────────────────────────────────


class KillSwitch:
    """Heartbeat-driven dead-man switch.

    The API deliberately provides no server-side dead-man switch (skills
    ``gotchas.md`` §13), so every bot must own one. If the strategy loop stalls
    past ``max_silence_s``, all open orders are cancelled.

    Cancellation is **verified**, not assumed: ``cancel_all`` is expected to
    re-read open orders and raise if any survive. A kill switch that reports
    success without confirming is worse than none, because it hides live
    exposure behind a reassuring log line.
    """

    def __init__(
        self,
        max_silence_s: float,
        cancel_all: Callable[[], None],
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.max_silence_s = float(max_silence_s)
        self._cancel_all = cancel_all
        self._log = log_fn or (lambda m: log.error(m))
        self._last_beat = time.monotonic()
        self._armed = True
        self._lock = threading.Lock()
        self._firing = False

    def heartbeat(self) -> None:
        self._last_beat = time.monotonic()

    @property
    def is_armed(self) -> bool:
        return self._armed

    def silence_s(self) -> float:
        return time.monotonic() - self._last_beat

    def check(self) -> bool:
        """Fire if the loop has gone quiet. Returns True when it fired."""
        if not self._armed:
            return False
        silence = self.silence_s()
        if silence <= self.max_silence_s:
            return False
        self.trigger(f"heartbeat timeout — {silence:.1f}s since last beat")
        return True

    def trigger(self, reason: str) -> None:
        with self._lock:
            if not self._armed or self._firing:
                return
            self._firing = True
            self._armed = False

        self._log(f"KILL SWITCH: {reason}")
        try:
            self._cancel_all()
            self._log("kill switch: all orders cancelled and verified")
        except Exception as exc:  # noqa: BLE001 - re-armed and re-raised
            # Re-arm so a later attempt can still fire. The exposure is real.
            with self._lock:
                self._armed = True
            self._log(f"kill switch: cancellation FAILED — {exc}")
            raise
        finally:
            with self._lock:
                self._firing = False

    def disarm(self) -> None:
        self._armed = False

    def rearm(self) -> None:
        self._armed = True
        self._last_beat = time.monotonic()


@dataclass
class ShutdownState:
    """Observable result of a shutdown, so tests need no process exit."""

    shutting_down: bool = False
    exit_code: int = 0
    reasons: List[str] = field(default_factory=list)


def install_shutdown_handlers(
    kill_switch: KillSwitch,
    timeout_s: float = 20.0,
    state: Optional[ShutdownState] = None,
    exit_fn: Optional[Callable[[int], None]] = None,
) -> ShutdownState:
    """Install SIGINT/SIGTERM handlers that cancel all orders before exiting.

    Exits non-zero when cancellation failed, so a supervisor can distinguish a
    clean shutdown from one that left orders resting on the book.

    ``state`` and ``exit_fn`` are injectable so the handler body is testable
    without terminating the test runner.
    """
    st = state or ShutdownState()
    do_exit = exit_fn or (lambda code: __import__("sys").exit(code))

    def _shutdown(reason: str) -> None:
        if st.shutting_down:
            return
        st.shutting_down = True
        st.reasons.append(reason)

        done = threading.Event()
        err: List[BaseException] = []

        def _run() -> None:
            try:
                kill_switch.trigger(reason)
            except BaseException as exc:  # noqa: BLE001
                err.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=_run, daemon=True, name="af-shutdown")
        worker.start()
        if not done.wait(timeout_s):
            log.error("shutdown: order cancellation timed out after %.0fs", timeout_s)
            st.exit_code = 1
        elif err:
            log.error("shutdown: %s", err[0])
            st.exit_code = 1
        do_exit(st.exit_code)

    def _handler(signum, _frame):  # noqa: ANN001 - signal handler signature
        name = signal.Signals(signum).name
        _shutdown(f"{name} — process asked to stop")

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
    except ValueError:
        # Not on the main thread (e.g. under a test runner). Handlers are a
        # best-effort convenience; the kill switch still works directly.
        log.debug("shutdown handlers not installed (not on the main thread)")

    st_shutdown = _shutdown  # exposed for tests
    setattr(st, "_shutdown", st_shutdown)
    return st
