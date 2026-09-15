"""
Finds candidate tokens to evaluate.

Honest note: this uses DexScreener's official "latest token profiles" feed,
not a raw mempool/websocket listener. That means we are NOT racing dedicated
sniper bots for the first-block entry — we accepted that tradeoff earlier
given free-tier infra. This scanner favors stability (documented, unlikely
to break) over raw speed.
"""

import logging
import time
import requests
import config

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
    profiles = fetch_latest_profiles()
    now_ms = time.time() * 1000

    for profile in profiles:
        address = profile.get("tokenAddress")
        if not address:
            continue
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
