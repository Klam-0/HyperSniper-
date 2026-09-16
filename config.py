"""
Central configuration for the trading bot.
ALL risk numbers live here so they're easy to find and change —
don't hardcode thresholds anywhere else in the codebase.
"""

import os

# --- SAFETY SWITCH -----------------------------------------------------
# When True: bot scans, scores, and logs decisions but places NO real trades.
# Set to False only after you've watched simulation logs and trust the logic.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"

# --- WALLET & RPC --------------------------------------------------------
SOLANA_PRIVATE_KEY = os.environ.get("SOLANA_PRIVATE_KEY")  # base58 string, Phantom export format
RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")  # swap for Helius/QuickNode URL when ready
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")  # your personal chat id, so the bot can push alerts unprompted

# --- WALLET SIZE / POSITION SIZING ---------------------------------------
# At small wallet sizes, only one position at a time makes sense —
# splitting $10 across multiple trades makes each trade too small to
# survive network fees and slippage.
MAX_CONCURRENT_POSITIONS = 1
POSITION_SIZE_SOL = 0.05          # ~ a few dollars per trade at small scale; raise once wallet grows
MAX_SLIPPAGE_BPS = 1500           # 15% max slippage, in basis points (Jupiter's unit)

# --- ENTRY RULES -----------------------------------------------------------
MIN_SCORE_TO_BUY = 9              # out of 12, same scale as the scorecard bot
MIN_LIQUIDITY_USD = 15000
MAX_TOP_HOLDER_PCT = 15           # reject if any single non-LP wallet holds >15% of supply
MIN_AGE_MINUTES = 10
