"""
Tracks MULTIPLE concurrent open positions (dict keyed by token address) and
P/L, persisted to a JSON file so state survives restarts.

REVISED from single-position mode: the bot now opens as many concurrent
$1 positions as available balance (live) or the DRY_RUN cap allows, not
just one at a time. Each position is tracked independently — opened,
monitored, and closed on its own, regardless of what any other open
position is doing.

P/L is computed as a ROLLING 24-hour window (based on trade_log timestamps),
not a calendar-day counter. See risk_manager.get_rolling_daily_pnl().
"""

import json
import logging
import os
import time
import config

log = logging.getLogger("position")

_DEFAULT_STATE = {
    "open_positions": {},   # dict: address -> position dict
    "circuit_breaker_until": None,  # ISO timestamp or None
    "trade_log": [],
}


def load_state():
    if not os.path.exists(config.STATE_FILE):
        return dict(_DEFAULT_STATE)
    try:
        with open(config.STATE_FILE, "r") as f:
            state = json.load(f)
        # Backward-compat: drop old fields from earlier single-position
        # versions of this file, and migrate a single leftover open
        # position into the new dict-keyed format so nothing silently
        # vanishes on upgrade.
        state.pop("daily_pnl_pct", None)
        state.pop("daily_date", None)
        old_single = state.pop("open_position", None)
        state.setdefault("open_positions", {})
        if old_single and old_single.get("address") not in state["open_positions"]:
            state["open_positions"][old_single["address"]] = old_single
        state.setdefault("trade_log", [])
        state.setdefault("circuit_breaker_until", None)
        return state
    except Exception as e:
        log.warning(f"Failed to load state, starting fresh: {e}")
        return dict(_DEFAULT_STATE)


def save_state(state: dict):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def open_position_count(state: dict) -> int:
    return len(state.get("open_positions", {}))


def is_position_open(state: dict, address: str) -> bool:
    return address in state.get("open_positions", {})


def open_position(state: dict, address: str, symbol: str, entry_price: float,
                   amount_sol: float, amount_usd: float, tokens_received: float):
    state.setdefault("open_positions", {})[address] = {
        "address": address,
        "symbol": symbol,
        "entry_price": entry_price,
        "amount_sol": amount_sol,
        "amount_usd": amount_usd,   # NEW: dollar size at entry, for $ P/L display
        "tokens_received": tokens_received,
        "tokens_remaining_pct": 100,
        "opened_at": time.time(),
        "peak_price": entry_price,
        "ladder_stage": 0,  # unused now (no more fixed ladder), kept for old-state compatibility
    }
    save_state(state)


def close_position(state: dict, address: str, exit_reason: str, pnl_pct: float, pnl_usd: float = 0):
    pos = state.get("open_positions", {}).pop(address, None)
    if pos:
        state["trade_log"].append({
            "symbol": pos["symbol"],
            "address": pos["address"],
            "entry_price": pos["entry_price"],
            "amount_usd": pos.get("amount_usd", 0),
            "opened_at": pos["opened_at"],
            "closed_at": time.time(),
            "exit_reason": exit_reason,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
        })
    save_state(state)


def log_trade_line(text: str):
    with open(config.LOG_FILE, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
