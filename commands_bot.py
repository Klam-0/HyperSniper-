"""
Telegram command interface — the kill switch. Runs alongside the trading
loop (see main.py) so you can /stop, /resume, or /status at any time.
"""

import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import config
import position as position_store
import risk_manager

log = logging.getLogger("commands")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = position_store.load_state()
    mode = "DRY RUN (simulation)" if config.DRY_RUN else "LIVE"
    positions = state.get("open_positions", {})
    cb = state.get("circuit_breaker_until")
    paused = cb and datetime.fromisoformat(cb) > datetime.now()
    rolling_pnl = risk_manager.get_rolling_daily_pnl(state)

    lines = [f"Mode: {mode}", f"Rolling 24h P/L: {rolling_pnl:+.1f}%"]
    lines.append(f"Circuit breaker: {'ACTIVE until ' + cb if paused else 'off'}")

    if positions:
        lines.append(f"\nOpen positions ({len(positions)}):")
        for addr, pos in positions.items():
            lines.append(
                f"- {pos['symbol']} @ ${pos['entry_price']:.8f} "
                f"(${pos.get('amount_usd', 0):.2f}) — `{addr}`"
            )
    else:
        lines.append("Open positions: none")

    lines.append(f"\nTotal trades logged: {len(state.get('trade_log', []))}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = position_store.load_state()
    until = datetime.now() + timedelta(days=3650)  # effectively indefinite, until /resume
    state["circuit_breaker_until"] = until.isoformat()
    position_store.save_state(state)
    await update.message.reply_text("🛑 Trading paused. No new positions will be opened. Send /resume to continue.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = position_store.load_state()
    state["circuit_breaker_until"] = None
    position_store.save_state(state)
    await update.message.reply_text("▶️ Trading resumed.")


def build_app():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("resume", resume))
    return app
