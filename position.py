"""
Tracks the current open position (single-position mode at small wallet
scale) and daily P/L, persisted to a JSON file so state survives restarts.
"""

import json
import logging
import os
import time
from datetime import date
import config

log = logging.getLogger("position")

_DEFAULT_STATE = {
    "open_position": None,   # dict or None
    "daily_pnl_pct": 0.0,
    "daily_date": str(date.today()),
    "circuit_breaker_until": None,  # ISO timestamp or None
    "trade_log": [],
}


def load_state():
    if not os.path.exists(config.STATE_FILE):
        return dict(_DEFAULT_STATE)
    try:
        with open(config.STATE_FILE, "r") as f:
            state = json.load(f)
        # Reset daily P/L if it's a new day
        if state.get("daily_date") != str(date.today()):
            state["daily_pnl_pct"] = 0.0
            state["daily_date"] = str(date.today())
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
        state["daily_pnl_pct"] += pnl_pct
        state["open_position"] = None
    save_state(state)


def log_trade_line(text: str):
    with open(config.LOG_FILE, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
