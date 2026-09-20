"""
Finds candidate tokens to evaluate.

Two discovery sources feed into this, merged together:
  1. DexScreener's "latest token profiles" feed (polling, documented, stable)
  2. mempool_listener's websocket-discovered pool addresses (real-time,
     hears about new Raydium/pump.fun pools within ~1s of creation)

Both sources funnel into the SAME scoring pipeline below — once an address
is known (from either source), its actual price/liquidity/volume/age still
comes from DexScreener's pair-data endpoint, which is the reliable,
already-working part of this system. The websocket only changes how fast
we HEAR ABOUT a token, not how we evaluate it.
"""

import logging
import time
import requests
import config
import mempool_listener

log = logging.getLogger("scanner")


def fetch_latest_profiles():
    """Pulls the latest token profiles DexScreener has indexed, across chains."""
    try:
        r = requests.get(f"{config.DEXSCREENER_BASE}/token-profiles/latest/v1", timeout=10)
        r.raise_for_status()
        data = r.json()
        return [d for d in data if d.get("chainId") == "solana"]
    except Exception as e:
        log.warning(f"Failed to fetch latest profiles: {e}")
        return []


def fetch_pair_data(address: str):
    """Full pair data (liquidity, volume, txns, age) for a given token address."""
    try:
        url = f"{config.DEXSCREENER_BASE}/latest/dex/tokens/{address}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        pairs.sort(key=lambda p: (p.get("liquidity", {}) or {}).get("usd", 0) or 0, reverse=True)
        return pairs[0]
    except Exception as e:
        log.warning(f"Failed to fetch pair data for {address}: {e}")
        return None


def get_candidates():
    """Returns a list of pair dicts worth scoring — already filtered to the
    configured age window, so downstream scoring doesn't waste calls on
    obviously-too-old or too-new tokens."""
    candidates = []
    now_ms = time.time() * 1000
    seen_addresses = set()

    # Source 1: DexScreener's polling feed (existing, reliable).
    profiles = fetch_latest_profiles()
    addresses_to_check = [(p.get("tokenAddress"), "dexscreener") for p in profiles if p.get("tokenAddress")]

    # Source 2: websocket-discovered pools (new — real-time).
    # A brand-new pool won't have DexScreener pair data indexed yet within
    # the first few seconds, so many of these will come back empty from
    # fetch_pair_data for a little while — that's expected, not an error;
    # they'll get picked up on a later scan once DexScreener catches up,
    # or via source 1 regardless.
    ws_discovered = mempool_listener.get_recent_discoveries(max_age_seconds=config.MAX_AGE_MINUTES * 60)
    addresses_to_check += [(addr, "websocket") for addr in ws_discovered]

    for address, source in addresses_to_check:
        if address in seen_addresses:
            continue
        seen_addresses.add(address)

        pair = fetch_pair_data(address)
        if not pair:
            continue

        created = pair.get("pairCreatedAt")
        if not created:
            continue
        age_minutes = (now_ms - created) / (1000 * 60)

        if config.MIN_AGE_MINUTES <= age_minutes <= config.MAX_AGE_MINUTES:
            candidates.append(pair)

    return candidates
