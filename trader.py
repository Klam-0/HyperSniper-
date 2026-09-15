"""
Main trading loop: scans for candidates, buys when one qualifies, monitors
the open position, sells according to the exit rules.

Safety notes baked into the flow:
  - Respects config.DRY_RUN throughout (see jupiter.py)
  - Never opens a new position while one is already open (single-position mode)
  - Checks the circuit breaker before every entry
  - Every decision is logged and pushed to Telegram
"""

import logging
import time
import config
import scanner
import scorer
import position as position_store
import risk_manager
from jupiter import swap
from wallet import get_public_key_str
from balances import get_token_balance_base_units, get_sol_balance_lamports
from security_checks import evaluate_safety
from notifier import send

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("trader")


def try_open_position(state: dict):
    if state.get("open_position"):
        return  # already holding something, single-position mode

    if risk_manager.circuit_breaker_active(state):
        return  # paused after hitting daily loss limit

    candidates = scanner.get_candidates()
    log.info(f"Scanned {len(candidates)} candidates")

    for pair in candidates:
        result = scorer.evaluate(pair)
        if not result["qualifies"]:
            continue

        log.info(f"Candidate qualifies: {result['symbol']} score={result['total_score']}")

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

    owner = get_public_key_str()
    balance = get_token_balance_base_units(owner, pos["address"])
    sell_amount = int(balance * sell_fraction)

    if sell_amount <= 0:
        log.warning(f"No token balance found to sell for {pos['symbol']} — closing position record anyway")
        pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100
        position_store.close_position(state, reason, pnl_pct)
        risk_manager.maybe_trigger_circuit_breaker(state)
        position_store.save_state(state)
        return

    swap_result = swap(pos["address"], config.SOL_MINT, sell_amount)
    pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100
    tag = "[SIMULATED] " if swap_result["simulated"] else ""

    if not swap_result["success"]:
        msg = f"⚠️ Sell failed for {pos['symbol']}: {swap_result['error']} — will retry next cycle"
        log.warning(msg)
        send(msg)
        return

    if sell_fraction >= 1.0:
        msg = f"{tag}🔴 Sold {pos['symbol']} — {reason} — P/L: {pnl_pct:+.1f}%"
        position_store.close_position(state, reason, pnl_pct)
        risk_manager.maybe_trigger_circuit_breaker(state)
    else:
        msg = f"{tag}🟡 Partial sell {pos['symbol']} ({sell_fraction*100:.0f}%) — {reason} — P/L: {pnl_pct:+.1f}%"
        pos["ladder_stage"] = pos.get("ladder_stage", 0) + 1
        pos["tokens_remaining_pct"] = pos.get("tokens_remaining_pct", 100) * (1 - sell_fraction)

    log.info(msg)
    send(msg)
    position_store.log_trade_line(msg)
    position_store.save_state(state)


def run_once():
    state = position_store.load_state()
    manage_open_position(state)
    try_open_position(state)
    position_store.save_state(state)


def main_loop():
    mode = "DRY RUN (simulation)" if config.DRY_RUN else "LIVE — real funds"
    send(f"🤖 Trading bot started in {mode} mode.")
    log.info(f"Starting main loop in {mode} mode. Scan interval: {config.SCAN_INTERVAL_SECONDS}s")

    while True:
        try:
            run_once()
        except Exception as e:
            log.exception(f"Error in main loop: {e}")
            send(f"⚠️ Bot error: {e}")
        time.sleep(config.SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main_loop()
