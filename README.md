# Meme Coin Trading Bot — Setup Guide

## ⚠️ Read this whole section before doing anything else

- This bot starts in **DRY_RUN (simulation) mode by default**. It will scan,
  score, and log everything it *would* do — buy, sell, stop-loss, take-profit
  — without touching a single real token or lamport. This is intentional:
  the code has never run against live APIs, and I want you watching its
  decisions for a while before it touches real money.
- **Never use a wallet that holds funds you care about.** Create a brand new
  burner wallet in Phantom, fund it with only the $10 (or whatever amount)
  you're fully OK losing completely, and use nothing else in it.
- **Never share your private key with anyone, ever, including pasting it
  into a chat, a public repo, or anywhere other than Railway's Variables
  tab.** Anyone with it can drain the wallet instantly and irreversibly.
- This bot does **not** compete with dedicated sniping bots on raw speed —
  we discussed this tradeoff. It uses free-tier infrastructure and DexScreener's
  documented API, not a private RPC + Jito bundles. It's built to avoid
  the worst traps (honeypots, rugs, panic dumps) and manage exits
  disciplined — not to win speed races.
- **Nothing here guarantees profit.** Meme coins are a documented
  negative-expected-value market for most retail traders. Treat any result,
  win or lose, as one data point, not proof the strategy works.

---

## What it does

1. Scans DexScreener's latest Solana token listings
2. Scores each candidate against liquidity, momentum, timing, and TWO
   independent safety checks (GoPlus + RugCheck)
3. If a token scores ≥9/12 AND passes both safety checks AND has
   liquidity >$15k → buys a small position (default 0.05 SOL)
4. Monitors the open position continuously:
   - Hard stop-loss at -25%
   - Take-profit ladder: sells 40% at 2x, 30% at 4x
   - Trailing stop on the remainder after the ladder fires
   - Time-decay exit if no 1.5x within 3 hours
   - Emergency exit if a top holder suddenly dumps a large % of supply
5. Pauses itself for 24h if daily losses hit -20% of wallet (circuit breaker)
6. Sends you a Telegram message for every single decision
7. Responds to `/status`, `/stop` (kill switch — pauses all new trades),
   and `/resume`

## Setup steps

### 1. Create a burner wallet
- Open Phantom (or any Solana wallet), create a **new** wallet (don't reuse
  an existing one)
- Fund it with only your test amount (e.g. $10 in SOL)
- Go to Settings → Export Private Key, copy it — you'll paste this into
  Railway's Variables tab, nowhere else

### 2. Get a Telegram bot token
- Same as before: message @BotFather → `/newbot` → copy the token
- To get your **chat ID** (needed for push alerts): message @userinfobot
  on Telegram, it'll reply with your numeric ID

### 3. Upload these files to GitHub
Same process as the scorecard bot — create a repo (private recommended,
given this handles a wallet key), upload all files in this folder.

### 4. Deploy on Railway
- New service → connect the GitHub repo
- Set **Custom Start Command**: `python main.py`
- Add these environment variables (Variables tab):

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | from @userinfobot |
| `SOLANA_PRIVATE_KEY` | your burner wallet's exported key |
| `DRY_RUN` | `true` (leave as true to start!) |
| `RPC_URL` | `https://api.mainnet-beta.solana.com` (fine for dry-run; get a free Helius URL before going live) |

- Deploy. Check logs for `Starting main loop in DRY RUN (simulation) mode`

### 5. Watch it run
- Message your bot `/status` any time
- Watch the Telegram alerts for a few days — do the entries make sense?
  Do the exits trigger the way you'd expect?
- **Do not flip DRY_RUN to false until you've reviewed real simulated
  decisions and are comfortable with the logic.**

### 6. Going live (only when ready)
- Sign up for a **free Helius RPC** (helius.dev) — much more reliable than
  the public endpoint
- Update `RPC_URL` to your Helius URL
- Change `DRY_RUN` to `false`
- Redeploy
- Watch closely, especially the first few trades

## Known limitations (be aware)

- **Not a raw sniper** — uses DexScreener's official feed, not a mempool
  listener. Won't win first-block races against dedicated sniping bots.
- **Single position at a time** — appropriate at small wallet size, but
  means the bot sits idle between trades rather than diversifying.
- **RugCheck/GoPlus can both lag on brand-new tokens** — the safety check
  treats "no data" as unverified (blocks the trade), not "assumed safe."
- **Free RPC rate limits** — the public Solana RPC can be slow/rate-limited
  under load; this affects both scanning and (if live) transaction
  confirmation speed.
- **This code has not been tested against live Solana transactions.**
  I built it carefully and it's syntactically correct, but real-world API
  responses can differ from documentation in ways that only show up in
  actual use. Expect to debug things together once it's running, the same
  way we did with the scorecard bot's Solana chain issue.

## Files

| File | Purpose |
|---|---|
| `config.py` | All risk rule numbers — the first place to look/adjust |
| `scanner.py` | Finds candidate tokens |
| `scorer.py` | Scores candidates, combines safety checks |
| `security_checks.py` | GoPlus + RugCheck dual verification |
| `wallet.py` | Loads your burner wallet's key |
| `jupiter.py` | Executes swaps (buy/sell) |
| `balances.py` | Reads real on-chain balances before selling |
| `position.py` | Tracks the open position + trade history (JSON file) |
| `risk_manager.py` | Circuit breaker + exit rule logic |
| `notifier.py` | Sends Telegram alerts |
| `commands_bot.py` | `/status`, `/stop`, `/resume` |
| `trader.py` | Main trading loop |
| `main.py` | Entry point — runs everything together |

**Not financial advice. You are responsible for any funds you put in this
wallet. Start in DRY_RUN, start small, and expect to lose test money as
part of learning whether this is worth pursuing further.**
