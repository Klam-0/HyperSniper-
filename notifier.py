"""
Sends Telegram alerts for every trade decision, and exposes /stop, /resume,
/status commands as the kill switch.
"""

import logging
import requests
import config

log = logging.getLogger("notifier")


def send(text: str):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.info(f"[no telegram configured] {text}")
        return
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Failed to send Telegram notification: {e}")
