"""
Two independent loops, running concurrently:

  1. position_monitor_loop() — watches ALL currently open positions' prices
     (config.DRY_RUN_MAX_CONCURRENT_POSITIONS in simulation, or as many as
     balance allows live), via DexScreener (300 req/min limit — generous).
     Runs fast (config.POSITION_MONITOR_INTERVAL_SECONDS) so stop-loss/
     trailing-stop react quickly to a crash, on EVERY open position.

  2. candidate_scan_loop() — scans for NEW candidates, including the full
     GoPlus + RugCheck safety checks (much more rate-limited), and opens
     as many new positions as available slots allow. Stays on
     config.SCAN_INTERVAL_SECONDS.

REVISED from single-position mode: the bot now runs MULTIPLE concurrent
$1 (config.POSITION_SIZE_USD) positions at once. In DRY_RUN, concurrency
is capped at a fixed number. In LIVE mode, the number of concurrent
positions scales with actual free wallet balance — a bigger deposit
naturally affords more simultaneous $1 trades, a smaller one fewer.

Safety notes baked into the flow:
  - Respects config.DRY_RUN throughout (see jupiter.py)
  - Never opens a second position in the SAME token while one is already open
  - Checks the circuit breaker before every entry
  - Every decision is logged and pushed to Telegram, including the token
    address and dollar amounts (entry size, P/L in both % and $)
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


def get_available_position_slots(state: dict) -> int:
    """How many NEW positions can be opened right now.

    DRY_RUN: fixed cap, independent of any balance math.
    LIVE: derived from actual free SOL balance converted to USD, minus a
    per-slot reserve for fees + token-account rent. This naturally scales
    with deposits — a bigger wallet affords more concurrent $1 positions,
    a smaller one affords fewer, with no separate "how much did I deposit"
    tracking needed, since free balance already reflects that."""
    if config.DRY_RUN:
        return max(0, config.DRY_RUN_MAX_CONCURRENT_POSITIONS - position_store.open_position_count(state))

    sol_price = scanner.get_sol_price_usd()
    if sol_price <= 0:
        log.warning("Could not get SOL price — refusing to size positions blind")
        return 0

    owner = get_public_key_str()
    free_sol = get_sol_balance_lamports(owner) / 1_000_000_000
    free_usd = free_sol * sol_price

    per_slot_cost = config.POSITION_SIZE_USD + config.LIVE_RESERVE_USD
    return max(0, int(free_usd // per_slot_cost))


def execute_buy(state: dict, result: dict, sol_price: float):
    """Shared buy logic — used for both normally-confirmed candidates and
    instantly-confirmed copy-trade signals. Sizes the trade in SOL terms
    from the configured USD amount, using the current SOL/USD price."""
    amount_sol = config.POSITION_SIZE_USD / sol_price
    amount_lamports = int(amount_sol * 1_000_000_000)

    swap_result = swap(config.SOL_MINT, result["address"], amount_lamports)

    if not swap_result["success"]:
        msg = f"⚠️ Buy failed for {result['symbol']} ({result['address']}): {swap_result['error']}"
        log.warning(msg)
        send(msg)
        return False

    entry_price = result["price_usd"]
    tokens_received = 0
    if swap_result["quote"]:
        tokens_received = int(swap_result["quote"].get("outAmount", 0))

    position_store.open_position(
        state, result["address"], result["symbol"], entry_price,
        amount_sol, config.POSITION_SIZE_USD, tokens_received,
    )
    tag = "[SIMULATED] " if swap_result["simulated"] else ""
    msg = (
        f"{tag}🟢 Bought {result['symbol']}\n"
        f"Address: `{result['address']}`\n"
        f"Entry: ${entry_price:.8f} — Size: ${config.POSITION_SIZE_USD:.2f} ({amount_sol:.5f} SOL)\n"
        f"Score: {result['total_score']}/12\n"
        f"Reasons:\n" + "\n".join(f"- {r}" for r in result["reasons"])
    )
    log.info(msg)
    send(msg)
    position_store.log_trade_line(msg)
    return True


def try_open_position(state: dict):
    if risk_manager.circuit_breaker_active(state):
        return  # paused after hitting the loss limit

    available_slots = get_available_position_slots(state)
    if available_slots <= 0:
        return

    candidates = scanner.get_candidates()
    log.info(f"Scanned {len(candidates)} candidates. Available position slots: {available_slots}")
    candidate_tracker.cleanup_stale()

    sol_price = scanner.get_sol_price_usd()
    if sol_price <= 0:
        log.warning("Could not get SOL price this cycle — skipping new entries")
        return

    for pair in candidates:
        if available_slots <= 0:
            break

        address = pair.get("baseToken", {}).get("address")
        if position_store.is_position_open(state, address):
            continue  # already holding this one, don't double up on the same token

        result = scorer.evaluate(pair)
        source = pair.get("_discovery_source", "dexscreener")
        is_copy_trade = source.startswith("copy:")

        if is_copy_trade:
            # Copy-trade candidates skip the multi-scan confirmation — the
            # watched wallet's own buy is treated as sufficient confirmation
            # on its own. Safety/score checks below still apply in full.
            confirmed = result["qualifies"]
        else:
            confirmed = candidate_tracker.record_and_check(
                result["address"], result["qualifies"], result["price_usd"]
            )

        if not result["qualifies"]:
            continue
        if not confirmed:
            log.info(
                f"Candidate qualifies but not yet confirmed: {result['symbol']} "
                f"score={result['total_score']}"
            )
            continue

        tag = " [COPY-TRADE]" if is_copy_trade else ""
        log.info(f"Candidate CONFIRMED{tag}: {result['symbol']} score={result['total_score']}")

        if execute_buy(state, result, sol_price):
            available_slots -= 1


def manage_one_position(state: dict, address: str, pos: dict, sol_price: float):
    pair = scanner.fetch_pair_data(address)
    if not pair:
        log.warning(f"Could not fetch current price for {pos['symbol']}, will retry next cycle")
        return

    current_price = float(pair.get("priceUsd", 0) or 0)
    if current_price <= 0:
        return

    if current_price > pos.get("peak_price", pos["entry_price"]):
        pos["peak_price"] = current_price

    # Approximate panic-dump detection: re-check top holder concentration
    _, _, top_holder_pct = evaluate_safety(address)
    prev_top_holder_pct = pos.get("last_top_holder_pct", top_holder_pct or 0)
    sudden_jump = 0
    if top_holder_pct is not None:
        sudden_jump = max(0, top_holder_pct - prev_top_holder_pct)
        pos["last_top_holder_pct"] = top_holder_pct

    should_exit, sell_fraction, reason = risk_manager.evaluate_exit(pos, current_price, sudden_jump)

    if not should_exit:
        return  # caller persists state after the full pass

    pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100
    amount_usd = pos.get("amount_usd", 0)
    pnl_usd = amount_usd * (pnl_pct / 100)

    if config.DRY_RUN:
        # Simulated sell: nothing to check on-chain (nothing was ever really
        # bought), so always report it properly rather than gating on a
        # real balance that will never exist in this mode.
        msg = (
            f"[SIMULATED] 🔴 Sold {pos['symbol']}\n"
            f"Address: `{address}`\n"
            f"{reason}\n"
            f"Entered with: ${amount_usd:.2f} — P/L: {pnl_pct:+.1f}% (${pnl_usd:+.2f})"
        )
        log.info(msg)
        send(msg)
        position_store.log_trade_line(msg)
        position_store.close_position(state, address, reason, pnl_pct, pnl_usd)
        risk_manager.maybe_trigger_circuit_breaker(state)
        return

    # LIVE mode — real balance check matters here.
    owner = get_public_key_str()
    balance = get_token_balance_base_units(owner, address)
    sell_amount = int(balance * sell_fraction)

    if sell_amount <= 0:
        msg = (
            f"⚠️ Expected to sell {pos['symbol']} (`{address}`) but found no wallet balance "
            f"— closing record. Entered with: ${amount_usd:.2f} — P/L: {pnl_pct:+.1f}% (${pnl_usd:+.2f})"
        )
        log.warning(msg)
        send(msg)
        position_store.close_position(state, address, reason, pnl_pct, pnl_usd)
        risk_manager.maybe_trigger_circuit_breaker(state)
        return

    swap_result = swap(address, config.SOL_MINT, sell_amount)

    if not swap_result["success"]:
        msg = f"⚠️ Sell failed for {pos['symbol']}: {swap_result['error']} — will retry next cycle"
        log.warning(msg)
        send(msg)
        return

    msg = (
        f"🔴 Sold {pos['symbol']}\n"
        f"Address: `{address}`\n"
        f"{reason}\n"
        f"Entered with: ${amount_usd:.2f} — P/L: {pnl_pct:+.1f}% (${pnl_usd:+.2f})"
    )
    log.info(msg)
    send(msg)
    position_store.log_trade_line(msg)
    position_store.close_position(state, address, reason, pnl_pct, pnl_usd)
    risk_manager.maybe_trigger_circuit_breaker(state)


def manage_open_positions(state: dict):
    positions = state.get("open_positions", {})
    if not positions:
        return

    sol_price = scanner.get_sol_price_usd()

    # Iterate over a snapshot — manage_one_position may close (mutate) the
    # underlying dict via position_store.close_position.
    for address, pos in list(positions.items()):
        manage_one_position(state, address, pos, sol_price)


def run_scan_cycle():
    with _state_lock:
        state = position_store.load_state()
        try_open_position(state)
        position_store.save_state(state)


def run_monitor_cycle():
    with _state_lock:
        state = position_store.load_state()
        manage_open_positions(state)
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
