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
# REVISED for aggressive/moonshot-hunting mode — explicit choice to accept
# more losses and more rug/wipeout risk in exchange for more shots at a
# real breakout winner. Loosened from the earlier conservative tuning.
MIN_SCORE_TO_BUY = 7              # lowered from 10 — lets more, riskier candidates through
MIN_LIQUIDITY_USD = 10000         # lowered from 15000 — still a floor (near-zero liquidity is
                                   # rug-bait, not moonshot potential, so this isn't removed)
MAX_TOP_HOLDER_PCT = 15           # UNCHANGED — this protects against a single wallet being
                                   # able to crash the price outright, not a risk worth taking
                                   # for upside, since it doesn't add moonshot potential
MIN_AGE_MINUTES = 5               # lowered from 25 — younger, more volatile tokens allowed
MAX_AGE_MINUTES = 90
MIN_BUY_SELL_RATIO_15M = 1.3
MAX_PRICE_CHANGE_1H_PCT = 300     # raised from 80 — tokens already pumping hard are no longer
                                   # auto-rejected; a real moonshot looks exactly like this
                                   # early on, so this filter worked against the new goal
MAX_VOLUME_TO_LIQUIDITY_RATIO = 80   # raised from 40 — loosened, though not removed entirely;
                                       # extreme wash trading still adds no real moonshot odds
MOMENTUM_CONFIRMATION_SCANS = 1   # lowered from 2 — faster entry, more shots on goal, less filtering

# --- EXIT RULES --------------------------------------------------------
# REVISED: replaced the fixed take-profit ladder (which capped gains at
# 2x/4x by design) with a single trailing stop that has NO upper limit —
# a position can keep running to 5x, 20x, 2000x, whatever, and only sells
# once it actually reverses from its peak. This is the direct tradeoff
# requested: real moonshot potential, in exchange for giving back a real
# chunk of any gain before the bot reacts (it only sells after a peak,
# never at one).
STOP_LOSS_PCT = -65               # loosened from -25 — a real big winner needs room to dip
                                   # significantly before recovering; a tight stop guarantees
                                   # getting shaken out before a real move happens
MOONSHOT_TRAILING_ACTIVATION_PCT = 15   # arms once price is up at least this much from entry
MOONSHOT_TRAILING_STOP_PCT = -35        # exit if price falls this much from its peak, whenever
                                          # that peak occurred — no ceiling, unlike the old ladder
MAX_HOLD_HOURS = 48               # raised from 3 — a real moonshot can take much longer to
                                   # develop than a few hours; this is now just a dead-token
                                   # catch-all, not a realistic cap on a genuine mover
HOLD_TARGET_MULTIPLIER = 1.2      # lowered from 1.5 — only force-exits if the token hasn't
                                   # even shown modest movement in 48h (clearly not happening)
PANIC_DUMP_SELL_PCT = 5           # UNCHANGED — this is rug-pull detection, not a risk knob;
                                   # a rug in progress has zero moonshot potential, only downside

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
