"""
Entry point. Runs the trading loop in a background thread and the Telegram
command bot (kill switch) in the main thread.
"""

import logging
import threading
import config
from trader import main_loop
from commands_bot import build_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("main")


def start_trading_thread():
    t = threading.Thread(target=main_loop, daemon=True)
    t.start()
    return t


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN.")
    if not config.TELEGRAM_CHAT_ID:
        log.warning("TELEGRAM_CHAT_ID not set — you won't receive push alerts, only /status on request.")
    if not config.DRY_RUN and not config.SOLANA_PRIVATE_KEY:
        raise RuntimeError("DRY_RUN is false but SOLANA_PRIVATE_KEY is not set. Refusing to start live.")

    log.info(f"DRY_RUN={config.DRY_RUN}")
    start_trading_thread()

    app = build_app()
    log.info("Starting Telegram command interface (/status, /stop, /resume)...")
    app.run_polling()


if __name__ == "__main__":
    main()
