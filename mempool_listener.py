"""
Real-time new-pool discovery via a direct websocket subscription to
Solana program logs — this is the actual "mempool-adjacent" speed
upgrade, replacing/supplementing the DexScreener polling-based discovery
with something that hears about new pools within roughly a second of
creation, not whenever DexScreener's indexer next picks it up.

HONEST SCOPE NOTE: this module handles DISCOVERY only (hearing about a
new pool the moment it's created). It does NOT compute prices from raw
pool reserves — that math (constant-product/bonding-curve formulas,
decimals handling, etc.) is easy to get subtly wrong, and a wrong price
feeding into a live stop-loss decision is a genuinely dangerous bug to
ship unverified. Once a candidate is discovered here, scorer.py still
fetches its actual price/liquidity/volume from DexScreener, same as
before — that data source has already been working correctly.

This connects to Helius's websocket endpoint (same API key as RPC_URL)
and subscribes to logs mentioning the Raydium AMM V4 program and the
pump.fun program — the two most common places new Solana meme tokens
get their trading pool.

Runs in its own background thread with its own asyncio event loop, so it
doesn't block or interfere with the two polling loops in trader.py. If
the websocket connection fails or drops, it logs the error, waits, and
reconnects — and the rest of the bot keeps working normally via
DexScreener polling regardless, since this is a supplement, not a
replacement, for discovery.
"""

import asyncio
import json
import logging
import re
import threading
import time
import config

log = logging.getLogger("mempool_listener")

# Program IDs for the two most common Solana meme-launch venues.
RAYDIUM_AMM_V4_PROGRAM_ID = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Thread-safe: addresses discovered via websocket, newest first, with
# discovery timestamps. scanner.py reads from this; this module only writes.
_discovered = []
_discovered_lock = threading.Lock()
_MAX_DISCOVERED = 200


def _get_ws_url():
    """Derives a websocket URL from the configured Helius RPC_URL. Only
    works for Helius URLs (http -> wss, same host/key). Returns None for
    other RPC providers, in which case this feature simply doesn't run —
    the bot still works fine via DexScreener polling alone."""
    url = config.RPC_URL or ""
    if "helius-rpc.com" not in url:
        return None
    return url.replace("https://", "wss://").replace("http://", "ws://")


def record_discovery(address: str):
    with _discovered_lock:
        _discovered.insert(0, {"address": address, "discovered_at": time.time()})
        del _discovered[_MAX_DISCOVERED:]


def get_recent_discoveries(max_age_seconds: int = 300):
    """Returns addresses discovered within the last max_age_seconds,
    newest first, deduplicated."""
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
            result.append(entry["address"])
        return result


def _extract_mint_from_logs(logs: list) -> str | None:
    """Best-effort extraction of a newly-created token mint address from
    program log lines. Log formats vary and aren't formally documented,
    so this looks for the common patterns rather than parsing strictly —
    a missed detection just means that one pool isn't caught early (falls
    back to DexScreener picking it up later), not an error."""
    for line in logs:
        # Pump.fun and Raydium initialize logs often include a base58
        # mint address directly in a "Program log:" line.
        match = re.search(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b", line)
        if match and ("mint" in line.lower() or "initialize" in line.lower() or "create" in line.lower()):
            return match.group(1)
    return None


async def _listen():
    import websockets

    ws_url = _get_ws_url()
    if not ws_url:
        log.warning(
            "RPC_URL doesn't look like a Helius URL — websocket discovery "
            "disabled. The bot still works fine via DexScreener polling alone."
        )
        return

    backoff = 5
    while True:
        try:
            log.info("Connecting to Helius websocket for real-time pool discovery...")
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                subscribe_msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [RAYDIUM_AMM_V4_PROGRAM_ID]},
                        {"commitment": "confirmed"},
                    ],
                }
                await ws.send(json.dumps(subscribe_msg))

                subscribe_msg_2 = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "logsSubscribe",
                    "params": [
                        {"mentions": [PUMPFUN_PROGRAM_ID]},
                        {"commitment": "confirmed"},
                    ],
                }
                await ws.send(json.dumps(subscribe_msg_2))

                log.info("Websocket connected and subscribed. Listening for new pools...")
                backoff = 5  # reset on successful connect

                async for message in ws:
                    try:
                        data = json.loads(message)
                        logs = (
                            data.get("params", {})
                            .get("result", {})
                            .get("value", {})
                            .get("logs", [])
                        )
                        if not logs:
                            continue
                        joined = " ".join(logs).lower()
                        if "initialize" not in joined and "create" not in joined:
                            continue
                        mint = _extract_mint_from_logs(logs)
                        if mint:
                            log.info(f"Discovered new pool candidate via websocket: {mint}")
                            record_discovery(mint)
                    except Exception as e:
                        log.debug(f"Failed to parse websocket message: {e}")

        except Exception as e:
            log.warning(f"Websocket disconnected or failed ({e}). Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)  # exponential backoff, capped at 60s


def start_background_listener():
    """Starts the websocket listener in its own thread with its own event
    loop. Safe to call even if the 'websockets' package isn't installed or
    the connection never succeeds — failures are caught and logged, and
    the rest of the bot is unaffected either way."""

    def _run():
        try:
            asyncio.run(_listen())
        except Exception as e:
            log.error(f"Mempool listener thread crashed: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
