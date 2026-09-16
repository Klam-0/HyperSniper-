"""
Portfolio-level protection: daily loss circuit breaker, and the exit-rule
evaluation (stop-loss, take-profit ladder, trailing stop, time decay,
panic-dump detection).
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
    sell_fraction is 0-1 of the REMAINING position to sell now.
    """
    entry = position["entry_price"]
    if entry <= 0:
        return False, 0, ""

    change_pct = ((current_price - entry) / entry) * 100
    multiple = current_price / entry

    # 1. Panic-dump detector — highest priority, exit everything immediately
    if top_holder_recent_sell_pct >= config.PANIC_DUMP_SELL_PCT:
        return True, 1.0, f"Panic-dump detected: top holder sold {top_holder_recent_sell_pct:.1f}% of supply"

    # 2. Hard stop-loss
    if change_pct <= config.STOP_LOSS_PCT:
        return True, 1.0, f"Stop-loss hit: {change_pct:.1f}%"

    # 3. Take-profit ladder
    ladder_stage = position.get("ladder_stage", 0)
    if ladder_stage < len(config.TAKE_PROFIT_LADDER):
        target_multiple, sell_frac = config.TAKE_PROFIT_LADDER[ladder_stage]
        if multiple >= target_multiple:
            return True, sell_frac, f"Take-profit rung {ladder_stage + 1} hit: {multiple:.2f}x"

    # 4. Trailing stop (only active after all ladder rungs have fired)
    if ladder_stage >= len(config.TAKE_PROFIT_LADDER):
        peak = position.get("peak_price", entry)
        drawdown_pct = ((current_price - peak) / peak) * 100
        if drawdown_pct <= config.TRAILING_STOP_PCT:
            return True, 1.0, f"Trailing stop hit: {drawdown_pct:.1f}% from peak"

    # 5. Time-decay exit
    hours_held = (time.time() - position["opened_at"]) / 3600
    if hours_held >= config.MAX_HOLD_HOURS and multiple < config.HOLD_TARGET_MULTIPLIER:
        return True, 1.0, f"Time-decay exit: {hours_held:.1f}h held, never reached {config.HOLD_TARGET_MULTIPLIER}x"

    return False, 0, ""
