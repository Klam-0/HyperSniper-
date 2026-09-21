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
POSITION_SIZE_SOL = 0.02          # lowered from 0.05 after a live buy failed with
                                   # "insufficient lamports" — 0.05 was over half the
                                   # ~0.096 SOL wallet, leaving no room for network fees
                                   # or leftover dust between trades
MAX_SLIPPAGE_BPS = 1500           # 15% max slippage, in basis points (Jupiter's unit)

# --- ENTRY RULES -----------------------------------------------------------
# Tightened after watching 3/3 simulated trades crash hard right after entry —
# the original rules caught single-snapshot momentum, which can just as
# easily mean "about to dump" as "about to run." These changes require more
# evidence before buying.
MIN_SCORE_TO_BUY = 10             # raised from 9 — stronger bar
MIN_LIQUIDITY_USD = 15000
MAX_TOP_HOLDER_PCT = 15           # reject if any single non-LP wallet holds >15% of supply
MIN_AGE_MINUTES = 25              # raised from 10 — gives early dump-and-runs more time to reveal themselves
MAX_AGE_MINUTES = 90
MIN_BUY_SELL_RATIO_15M = 1.3
MAX_PRICE_CHANGE_1H_PCT = 80      # NEW: reject if already up >80% in the last hour — likely buying the top
MAX_VOLUME_TO_LIQUIDITY_RATIO = 40   # NEW: reject if 24h volume is more than 40x liquidity — likely wash trading
MOMENTUM_CONFIRMATION_SCANS = 2   # NEW: a token must qualify on this many CONSECUTIVE scans before buying,
                                   # not just once — filters out one-off spikes that don't hold

# --- EXIT RULES --------------------------------------------------------
STOP_LOSS_PCT = -25               # hard exit if position drops this much
TAKE_PROFIT_LADDER = [            # (gain_multiplier, fraction_of_position_to_sell)
    (2.0, 0.40),                  # sell 40% at 2x — recover capital + buffer
    (4.0, 0.30),                  # sell another 30% at 4x
]
TRAILING_STOP_PCT = -20           # after ladder triggers, trail remaining position by this %
# NEW: protects gains on a token that pumps but never reaches the first
# take-profit rung (2x) before reversing. Before this existed, such a
# position had zero exit protection on the way back down until it either
# hit stop-loss (-25% from entry) or the 3h time-decay exit — potentially
# giving back a real, meaningful gain with nothing catching it.
EARLY_TRAILING_ACTIVATION_PCT = 20   # only arms once price is up at least this much from entry
EARLY_TRAILING_STOP_PCT = -20        # exit if price falls this much from its peak, pre-ladder
MAX_HOLD_HOURS = 3                # exit regardless of P/L if no 1.5x within this window
HOLD_TARGET_MULTIPLIER = 1.5
PANIC_DUMP_SELL_PCT = 5           # emergency-exit if a top holder sells this % of supply in one tx

# --- PORTFOLIO-LEVEL PROTECTION -------------------------------------------
DAILY_LOSS_CIRCUIT_BREAKER_PCT = 20   # pause trading 24h if daily losses hit this % of wallet
SCAN_INTERVAL_SECONDS = 10            # candidate scanning — includes GoPlus + RugCheck safety
                                       # checks, which are far more rate-limited than price data,
                                       # so this stays slower than the position monitor below
# NEW: separate, much faster loop that ONLY checks the price of the position
# you're currently holding (no safety re-checks needed every cycle — those
# run as part of candidate scanning and once per monitor cycle for the
# panic-dump check). DexScreener's price endpoint allows 300 req/min, so
# checking one token every 2s (30 req/min) is well within budget. This is
# what actually improves stop-loss reaction speed — scanning MANY
# candidates faster would hit rate limits long before this would.
POSITION_MONITOR_INTERVAL_SECONDS = 2

# --- DATA SOURCES ----------------------------------------------------------
DEXSCREENER_BASE = "https://api.dexscreener.com"
GOPLUS_EVM_BASE = "https://api.gopluslabs.io/api/v1/token_security"
GOPLUS_SOLANA_URL = "https://api.gopluslabs.io/api/v1/solana/token_security"
RUGCHECK_BASE = "https://api.rugcheck.xyz/v1"
JUPITER_API_KEY = os.environ.get("JUPITER_API_KEY")  # optional — get free at portal.jup.ag
# Jupiter retired quote-api.jup.ag (Oct 2025). Use lite-api.jup.ag (free, no key)
# or api.jup.ag (with a free API key from portal.jup.ag) — the latter is more
# future-proof since Jupiter has signaled lite-api.jup.ag will eventually require
# migration too.
JUPITER_BASE = "https://api.jup.ag/swap/v1" if JUPITER_API_KEY else "https://lite-api.jup.ag/swap/v1"
JUPITER_QUOTE_URL = f"{JUPITER_BASE}/quote"
JUPITER_SWAP_URL = f"{JUPITER_BASE}/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"  # wrapped SOL, used as the input mint for buys

# NEW: copy-trade discovery. Add specific wallet addresses here to have the
# bot treat their buys as an additional candidate-discovery signal (still
# subject to all the normal safety/scoring checks — this doesn't bypass
# them). We have no way to auto-find "good" wallets for free — that's
# proprietary data GMGN keeps paywalled. Find candidates manually via
# Solscan or Birdeye's public top-trader views, then paste addresses here.
# Empty by default — copy-trade discovery is inactive until you add some.
WATCHED_WALLETS = [
    "ardinRsN1mNYVeoJWTBsWeYeXvuR9UUDGMsCDKpb6AT",  # unverified — no public attribution found
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",  # "Cupsey" (@Cupseyy) — confirmed via Lookonchain + press
    "BTf4A2exGK9BCVDNzy65b9dUzXgMqB4weVkvTMFQsadd",  # "@Kevsznx" — confirmed via Raybot's tracked KOL list
]

# --- PERSISTENCE ------------------------------------------------------------
STATE_FILE = "bot_state.json"   # tracks open position + daily P/L across restarts
LOG_FILE = "trades.log"         # human-readable trade history
