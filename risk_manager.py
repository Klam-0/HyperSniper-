"""
Portfolio-level protection: daily loss circuit breaker, and the exit-rule
evaluation (stop-loss, unlimited-upside trailing stop, time decay,
panic-dump detection).

REVISED for aggressive/moonshot-hunting mode: the old fixed take-profit
ladder (sell 40% at 2x, 30% at 4x) has been removed entirely. It
guaranteed giving up most of any position once it merely doubled — the
opposite of what "let a real winner run to 2000%" requires. Replaced with
a single trailing stop that has no upper limit: a position rides for as
long as it keeps making new highs, and only sells once it actually falls
back a set % from wherever its peak ends up being — whether that peak is
2x or 200x.
"""

import logging
import time
from datetime import datetime, timedelta
import config

log = logging.getLogger("risk")


def get_rolling_daily_pnl(state: dict) -> float:
    """Sums pnl_pct for trades CLOSED within the last 24 hours — a rolling
    window, not a calendar-day counter. This avoids the bug where a loss
    right after midnight looked like a fresh crash instead of one trade
    landing in a freshly-reset, misleadingly-empty bucket."""
    cutoff = time.time() - (24 * 3600)
    recent = [t for t in state.get("trade_log", []) if t.get("closed_at", 0) >= cutoff]
    return sum(t.get("pnl_pct", 0) for t in recent)


def circuit_breaker_active(state: dict) -> bool:
    cb_until = state.get("circuit_breaker_until")
    if not cb_until:
        return False
    return datetime.fromisoformat(cb_until) > datetime.now()


def maybe_trigger_circuit_breaker(state: dict):
    rolling_pnl = get_rolling_daily_pnl(state)
    if rolling_pnl <= -config.DAILY_LOSS_CIRCUIT_BREAKER_PCT:
        until = datetime.now() + timedelta(hours=24)
        state["circuit_breaker_until"] = until.isoformat()
        log.warning(f"Circuit breaker triggered — rolling 24h loss hit {rolling_pnl:.1f}%. Pausing until {until}.")
        return True
    return False


def evaluate_exit(position: dict, current_price: float, top_holder_recent_sell_pct: float = 0):
    """
    Returns (should_exit: bool, sell_fraction: float, reason: str) or
    (False, 0, "") if the position should keep running.
    sell_fraction is always 1.0 now (no more partial ladder sells) — a
    position either keeps running whole, or exits whole.
    """
    entry = position["entry_price"]
    if entry <= 0:
        return False, 0, ""

    change_pct = ((current_price - entry) / entry) * 100
    multiple = current_price / entry

    # 1. Panic-dump detector — highest priority, exit everything immediately.
    # UNCHANGED: this is rug-pull detection, not a risk/reward knob. A rug
    # has zero moonshot potential, so there's no version of "maximize
    # profit" that benefits from loosening this.
    if top_holder_recent_sell_pct >= config.PANIC_DUMP_SELL_PCT:
        return True, 1.0, f"Panic-dump detected: top holder sold {top_holder_recent_sell_pct:.1f}% of supply"

    # 2. Hard stop-loss — loosened from -25% to -65%. A real moonshot needs
    # room to dip significantly before recovering; the old tight stop would
    # guarantee getting shaken out before a real move could develop.
    if change_pct <= config.STOP_LOSS_PCT:
        return True, 1.0, f"Stop-loss hit: {change_pct:.1f}%"

    # 3. Unlimited-upside trailing stop — replaces the old fixed ladder.
    # Once a position is up enough to have real gains worth protecting, it
    # arms and simply tracks the peak, no matter how high that peak goes.
    # It only sells on an actual reversal from that peak — never at a fixed
    # price target, so a real winner is never capped.
    peak = position.get("peak_price", entry)
    peak_gain_pct = ((peak - entry) / entry) * 100
    if peak_gain_pct >= config.MOONSHOT_TRAILING_ACTIVATION_PCT:
        drawdown_pct = ((current_price - peak) / peak) * 100
        if drawdown_pct <= config.MOONSHOT_TRAILING_STOP_PCT:
            return True, 1.0, (
                f"Trailing stop hit: {drawdown_pct:.1f}% from peak "
                f"(peak was {multiple:.2f}x entry)"
            )

    # 4. Time-decay exit — loosened from 3h to 48h. Now just a dead-token
    # catch-all (hasn't even shown modest movement in 2 days), not a
    # realistic cap on a genuine multi-hour or multi-day mover.
    hours_held = (time.time() - position["opened_at"]) / 3600
    if hours_held >= config.MAX_HOLD_HOURS and multiple < config.HOLD_TARGET_MULTIPLIER:
        return True, 1.0, f"Time-decay exit: {hours_held:.1f}h held, never reached {config.HOLD_TARGET_MULTIPLIER}x"

    return False, 0, ""
