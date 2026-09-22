"""
Watches specific wallet addresses (config.WATCHED_WALLETS) for new buys,
via a Helius websocket subscription, and surfaces those tokens as
candidates — same idea as mempool_listener.py, but the discovery trigger
is "a wallet you chose just bought something" instead of "a new pool was
created."

IMPORTANT — read before using:
We have no way to auto-discover which wallets are actually good traders.
That curated "smart money" ranking is exactly the proprietary data GMGN
keeps behind a paywall (see the callout API discussion). This module only
watches wallets YOU tell it to watch, via config.WATCHED_WALLETS. Finding
good wallets to add is on you — e.g. browsing Solscan or Birdeye's public
"top traders" leaderboards for a token that already pumped, and noting
which wallets bought early. This is manual research, not something the
bot can automate for free.

SAFETY DESIGN: a wallet buying a token is treated as a DISCOVERY signal
only — it does NOT bypass the existing safety/scoring pipeline. The token
still has to clear the same liquidity, safety (GoPlus + RugCheck),
wash-trading, and pump-distance checks as anything else. Blindly trusting
"a wallet I'm told is good bought this" without independent verification
would undo every protection built so far — copy trading is also one of
the most actively gamed signals in this market (fake "smart" wallets
exist specifically to bait copiers, as we discussed when comparing
existing platforms like Trojan).

Unlike the pool-discovery listener, this uses getTransaction (a
well-documented, structured RPC call) rather than regex-parsing raw log
text — token balance changes (preTokenBalances/postTokenBalances) reliably
show what was bought, in which direction, which is much less fragile than
pattern-matching log lines.
"""

import asyncio
import json
import logging
import threading
import time
import requests
import config
import discovery_events

log = logging.getLogger("wallet_watcher")

_discovered = []
_discovered_lock = threading.Lock()
_MAX_DISCOVERED = 200


def _get_ws_url():
    url = config.RPC_URL or ""
    if "helius-rpc.com" not in url:
        return None
    return url.replace("https://", "wss://").replace("http://", "ws://")


def record_discovery(address: str, watched_wallet: str):
    with _discovered_lock:
        _discovered.insert(0, {
            "address": address,
            "watched_wallet": watched_wallet,
            "discovered_at": time.time(),
        })
        del _discovered[_MAX_DISCOVERED:]
    discovery_events.new_discovery.set()


def get_recent_discoveries(max_age_seconds: int = 300):
    cutoff = time.time() - max_age_seconds
    with _discovered_lock:
        seen = set()
        result = []
        for entry in _discovered:
            if entry["discovered_at"] < cutoff:
                break
            if entry["address"] in seen:
                continue
            seen.add(entry["address"])
            result.append(entry)
        return result


def _fetch_transaction(signature: str):
    """Fetches the full parsed transaction so we can read actual token
    balance changes, rather than guessing from log text."""
    try:
        r = requests.post(
            config.RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("result")
    except Exception as e:
        log.debug(f"Failed to fetch transaction {signature}: {e}")
        return None


def _extract_bought_token(tx: dict, watched_wallet: str):
    """Compares pre/post token balances for the watched wallet to find a
    token whose balance INCREASED — i.e. something it bought. Returns the
    mint address, or None if this transaction wasn't a buy (could be a
    sell, an unrelated transaction, or the wallet just being mentioned
    incidentally)."""
    try:
        meta = tx.get("meta", {})
        pre = {b["mint"]: b for b in meta.get("preTokenBalances", []) if b.get("owner") == watched_wallet}
        post = {b["mint"]: b for b in meta.get("postTokenBalances", []) if b.get("owner") == watched_wallet}

        for mint, post_bal in post.items():
            if mint == config.SOL_MINT:
                continue  # ignore SOL itself, we only care about the token side
            pre_amount = float(pre.get(mint, {}).get("uiTokenAmount", {}).get("uiAmount") or 0)
            post_amount = float(post_bal.get("uiTokenAmount", {}).get("uiAmount") or 0)
            if post_amount > pre_amount:
                return mint  # balance went up — this wallet bought it
        return None
    except Exception as e:
        log.debug(f"Failed to extract bought token: {e}")
        return None


async def _watch_wallet(wallet_address: str):
    import websockets

    ws_url = _get_ws_url()
    if not ws_url:
        return

    backoff = 5
    while True:
        try:
            log.info(f"Watching wallet for buys: {wallet_address}")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [wallet_address]},
                        {"commitment": "confirmed"},
                    ],
                }
                await ws.send(json.dumps(subscribe_msg))
                backoff = 5

                async for message in ws:
                    try:
                        data = json.loads(message)
                        result = data.get("params", {}).get("result", {}).get("value", {})
                        signature = result.get("signature")
                        if not signature:
                            continue

                        tx = _fetch_transaction(signature)
                        if not tx:
                            continue
                        mint = _extract_bought_token(tx, wallet_address)
                        if mint:
                            log.info(f"Watched wallet {wallet_address[:8]}... bought: {mint}")
                            record_discovery(mint, wallet_address)
                    except Exception as e:
                        log.debug(f"Failed to process message for {wallet_address}: {e}")

        except Exception as e:
            log.warning(f"Wallet watch for {wallet_address[:8]}... disconnected ({e}). Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def _watch_all():
    wallets = [w.strip() for w in (config.WATCHED_WALLETS or []) if w.strip()]
    if not wallets:
        log.info("No wallets configured in WATCHED_WALLETS — copy-trade discovery is inactive.")
        return
    await asyncio.gather(*[_watch_wallet(w) for w in wallets])


def start_background_watcher():
    """Safe to call even with an empty WATCHED_WALLETS list or no
    'websockets' package — failures are caught and logged, and the rest
    of the bot is unaffected either way."""

    def _run():
        try:
            asyncio.run(_watch_all())
        except Exception as e:
            log.error(f"Wallet watcher thread crashed: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
