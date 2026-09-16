"""
Tracks the current open position (single-position mode at small wallet
scale) and P/L, persisted to a JSON file so state survives restarts.

P/L is computed as a ROLLING 24-hour window (based on trade_log timestamps),
not a calendar-day counter. The original calendar-day version reset to zero
at midnight regardless of when trades actually happened, which caused a
single loss right after midnight to look like a fresh crash instead of
what it was — one trade's result landing in an empty bucket. See
risk_manager.get_rolling_daily_pnl().
"""

import json
import logging
import os
import time
import config

log = logging.getLogger("position")

_DEFAULT_STATE = {
    "open_position": None,   # dict or None
    "circuit_breaker_until": None,  # ISO timestamp or None
    "trade_log": [],
}


def load_state():
    if not os.path.exists(config.STATE_FILE):
        return dict(_DEFAULT_STATE)
    try:
        with open(config.STATE_FILE, "r") as f:
            state = json.load(f)
        # Backward-compat: drop old calendar-day fields if present from a
        # prior version of this file.
        state.pop("daily_pnl_pct", None)
        state.pop("daily_date", None)
        state.setdefault("trade_log", [])
        state.setdefault("open_position", None)
        state.setdefault("circuit_breaker_until", None)
        return state
    except Exception as e:
        log.warning(f"Failed to load state, starting fresh: {e}")
        return dict(_DEFAULT_STATE)


def save_state(state: dict):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def open_position(state: dict, address: str, symbol: str, entry_price: float,
                   amount_sol: float, tokens_received: float):
    state["open_position"] = {
        "address": address,
        "symbol": symbol,
        "entry_price": entry_price,
        "amount_sol": amount_sol,
        "tokens_received": tokens_received,
        "tokens_remaining_pct": 100,
        "opened_at": time.time(),
        "peak_price": entry_price,
        "ladder_stage": 0,  # how many take-profit rungs already hit
    }
    save_state(state)


def close_position(state: dict, exit_reason: str, pnl_pct: float):
    pos = state.get("open_position")
    if pos:
        state["trade_log"].append({
            "symbol": pos["symbol"],
            "address": pos["address"],
            "entry_price": pos["entry_price"],
            "opened_at": pos["opened_at"],
            "closed_at": time.time(),
            "exit_reason": exit_reason,
            "pnl_pct": pnl_pct,
        })
        state["open_position"] = None
    save_state(state)


def log_trade_line(text: str):
    with open(config.LOG_FILE, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
