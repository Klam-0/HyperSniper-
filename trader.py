"""
Two independent loops, running concurrently:

  1. position_monitor_loop() — watches the CURRENTLY OPEN position's price
     only, via DexScreener (300 req/min limit — generous). Runs fast
     (config.POSITION_MONITOR_INTERVAL_SECONDS) so stop-loss/take-profit/
     trailing-stop react quickly to a crash.

  2. candidate_scan_loop() — scans for NEW candidates, including the full
     GoPlus + RugCheck safety checks (much more rate-limited). Stays on
     config.SCAN_INTERVAL_SECONDS.

Splitting these apart is what lets the bot react quickly to a holder dump
on an open position without needing to hammer the safety-check APIs at
the same fast pace — those two things have very different rate-limit
budgets, so they run on different clocks.

Safety notes baked into the flow:
  - Respects config.DRY_RUN throughout (see jupiter.py)
  - Never opens a new position while one is already open (single-position mode)
  - Checks the circuit breaker before every entry
  - Every decision is logged and pushed to Telegram
  - A lock protects the shared state file from being written by both loops
    at the same time
"""

import logging
import threading
import time
import config
import scanner
import scorer
import position as position_store
import risk_manager
import candidate_tracker
import mempool_listener
import discovery_events
import wallet_watcher
from jupiter import swap
from wallet import get_public_key_str
from balances import get_token_balance_base_units, get_sol_balance_lamports
from security_checks import evaluate_safety
from notifier import send

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("trader")

_state_lock = threading.Lock()


def try_open_position(state: dict):
    if state.get("open_position"):
        return  # already holding something, single-position mode

    if risk_manager.circuit_breaker_active(state):
        return  # paused after hitting daily loss limit

    candidates = scanner.get_candidates()
    log.info(f"Scanned {len(candidates)} candidates")
    candidate_tracker.cleanup_stale()

    for pair in candidates:
        result = scorer.evaluate(pair)
        source = pair.get("_discovery_source", "dexscreener")
        is_copy_trade = source.startswith("copy:")

        if is_copy_trade:
            # NEW, per explicit choice made after discussing the tradeoff:
            # copy-trade candidates skip the multi-scan confirmation
            # requirement — the watched wallet's own buy is treated as
            # sufficient confirmation on its own. This trades some of our
            # independent verification for speed, ONLY for this source.
            # Safety/score checks (below) still apply in full regardless.
            confirmed = result["qualifies"]
        else:
            # NEW: require sustained qualification across multiple
            # consecutive scans, not a single snapshot — this is the main
            # fix after 3/3 simulated trades crashed right after entry.
            confirmed = candidate_tracker.record_and_check(
                result["address"], result["qualifies"], result["price_usd"]
            )

        if not result["qualifies"]:
            continue
        if not confirmed:
            log.info(
                f"Candidate qualifies but not yet confirmed across "
                f"{config.MOMENTUM_CONFIRMATION_SCANS} scans: {result['symbol']} "
                f"score={result['total_score']}"
            )
            continue

        tag = " [COPY-TRADE]" if is_copy_trade else ""
        log.info(f"Candidate CONFIRMED{tag}: {result['symbol']} score={result['total_score']}")

        amount_lamports = int(config.POSITION_SIZE_SOL * 1_000_000_000)
        swap_result = swap(config.SOL_MINT, result["address"], amount_lamports)

        if not swap_result["success"]:
            msg = f"⚠️ Buy failed for {result['symbol']}: {swap_result['error']}"
            log.warning(msg)
            send(msg)
            continue

        entry_price = result["price_usd"]
        tokens_received = 0
        if swap_result["quote"]:
            tokens_received = int(swap_result["quote"].get("outAmount", 0))

        position_store.open_position(
            state, result["address"], result["symbol"], entry_price,
            config.POSITION_SIZE_SOL, tokens_received,
        )
        tag = "[SIMULATED] " if swap_result["simulated"] else ""
        msg = (
            f"{tag}🟢 Bought {result['symbol']} at ${entry_price:.8f}\n"
            f"Score: {result['total_score']}/12\n"
            f"Reasons:\n" + "\n".join(f"- {r}" for r in result["reasons"])
        )
        log.info(msg)
        send(msg)
        position_store.log_trade_line(msg)
        return  # one entry per cycle is enough


def manage_open_position(state: dict):
    pos = state.get("open_position")
    if not pos:
        return

    pair = scanner.fetch_pair_data(pos["address"])
    if not pair:
        log.warning(f"Could not fetch current price for {pos['symbol']}, will retry next cycle")
        return

    current_price = float(pair.get("priceUsd", 0) or 0)
    if current_price <= 0:
        return

    if current_price > pos.get("peak_price", pos["entry_price"]):
        pos["peak_price"] = current_price

    # Approximate panic-dump detection: re-check top holder concentration
    _, _, top_holder_pct = evaluate_safety(pos["address"])
    prev_top_holder_pct = pos.get("last_top_holder_pct", top_holder_pct or 0)
    sudden_jump = 0
    if top_holder_pct is not None:
        sudden_jump = max(0, top_holder_pct - prev_top_holder_pct)
        pos["last_top_holder_pct"] = top_holder_pct

    should_exit, sell_fraction, reason = risk_manager.evaluate_exit(pos, current_price, sudden_jump)

    if not should_exit:
        position_store.save_state(state)  # persist peak_price / last_top_holder_pct updates
        return

    if config.DRY_RUN:
        # Simulated sell: nothing to check on-chain (nothing was ever really
        # bought), so don't gate the notification on a real balance — that
        # was the bug. Always report the simulated sell properly.
        pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100
        if sell_fraction >= 1.0:
            msg = f"[SIMULATED] 🔴 Sold {pos['symbol']} — {reason} — P/L: {pnl_pct:+.1f}%"
            position_store.close_position(state, reason, pnl_pct)
            risk_manager.maybe_trigger_circuit_breaker(state)
        else:
            msg = f"[SIMULATED] 🟡 Partial sell {pos['symbol']} ({sell_fraction*100:.0f}%) — {reason} — P/L: {pnl_pct:+.1f}%"
            pos["ladder_stage"] = pos.get("ladder_stage", 0) + 1
            pos["tokens_remaining_pct"] = pos.get("tokens_remaining_pct", 100) * (1 - sell_fraction)
        log.info(msg)
        send(msg)
        position_store.log_trade_line(msg)
        position_store.save_state(state)
        return

    # LIVE mode from here on — real balance check matters, since we need to
    # know exactly how many tokens are actually sitting in the wallet.
    owner = get_public_key_str()
    balance = get_token_balance_base_units(owner, pos["address"])
    sell_amount = int(balance * sell_fraction)

    if sell_amount <= 0:
        log.warning(f"No real token balance found to sell for {pos['symbol']} — closing position record anyway")
        pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100
        msg = f"⚠️ Expected to sell {pos['symbol']} but found no wallet balance — closing record. P/L: {pnl_pct:+.1f}%"
        send(msg)
        position_store.close_position(state, reason, pnl_pct)
        risk_manager.maybe_trigger_circuit_breaker(state)
        position_store.save_state(state)
        return

    swap_result = swap(pos["address"], config.SOL_MINT, sell_amount)
    pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100

    if not swap_result["success"]:
        msg = f"⚠️ Sell failed for {pos['symbol']}: {swap_result['error']} — will retry next cycle"
        log.warning(msg)
        send(msg)
        return

    if sell_fraction >= 1.0:
        msg = f"🔴 Sold {pos['symbol']} — {reason} — P/L: {pnl_pct:+.1f}%"
        position_store.close_position(state, reason, pnl_pct)
        risk_manager.maybe_trigger_circuit_breaker(state)
    else:
        msg = f"🟡 Partial sell {pos['symbol']} ({sell_fraction*100:.0f}%) — {reason} — P/L: {pnl_pct:+.1f}%"
        pos["ladder_stage"] = pos.get("ladder_stage", 0) + 1
        pos["tokens_remaining_pct"] = pos.get("tokens_remaining_pct", 100) * (1 - sell_fraction)

    log.info(msg)
    send(msg)
    position_store.log_trade_line(msg)
    position_store.save_state(state)


def run_scan_cycle():
    with _state_lock:
        state = position_store.load_state()
        try_open_position(state)
        position_store.save_state(state)


def run_monitor_cycle():
    with _state_lock:
        state = position_store.load_state()
        manage_open_position(state)
        position_store.save_state(state)


def position_monitor_loop():
    """Fast loop: only watches the open position's price. Idle (cheap) when
    there's nothing open."""
    log.info(f"Position monitor loop started. Interval: {config.POSITION_MONITOR_INTERVAL_SECONDS}s")
    while True:
        try:
            run_monitor_cycle()
        except Exception as e:
            log.exception(f"Error in position monitor loop: {e}")
        time.sleep(config.POSITION_MONITOR_INTERVAL_SECONDS)


def candidate_scan_loop():
    """Slower loop: scans for new candidates, including full safety checks.
    Wakes up immediately when mempool_listener or wallet_watcher discovers
    something, instead of always waiting out the full interval — removes
    up to SCAN_INTERVAL_SECONDS of pure dead time on a fresh discovery."""
    log.info(f"Candidate scan loop started. Interval: {config.SCAN_INTERVAL_SECONDS}s")
    while True:
        try:
            run_scan_cycle()
        except Exception as e:
            log.exception(f"Error in candidate scan loop: {e}")
            send(f"⚠️ Bot error: {e}")

        discovery_events.new_discovery.wait(timeout=config.SCAN_INTERVAL_SECONDS)
        discovery_events.new_discovery.clear()


def main_loop():
    """Starts both loops. The monitor loop runs in a background thread; the
    scan loop runs in the calling thread (main.py already wraps this whole
    function in its own thread, so this becomes: 2 loops total alongside
    the Telegram command interface)."""
    mode = "DRY RUN (simulation)" if config.DRY_RUN else "LIVE — real funds"
    send(f"🤖 Trading bot started in {mode} mode.")
    log.info(
        f"Starting in {mode} mode. Position monitor: "
        f"{config.POSITION_MONITOR_INTERVAL_SECONDS}s, candidate scan: {config.SCAN_INTERVAL_SECONDS}s"
    )

    monitor_thread = threading.Thread(target=position_monitor_loop, daemon=True)
    monitor_thread.start()

    mempool_listener.start_background_listener()
    wallet_watcher.start_background_watcher()

    candidate_scan_loop()  # runs in this thread, blocking


if __name__ == "__main__":
    main_loop()
