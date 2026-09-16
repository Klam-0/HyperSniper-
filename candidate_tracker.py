"""
Tracks candidates across scan cycles so the bot requires SUSTAINED
qualification (config.MOMENTUM_CONFIRMATION_SCANS consecutive scans)
before buying, instead of acting on a single snapshot — AND that the
price hasn't started falling between those scans.

The price check was added after watching a token get bought 3x in a row
at successively LOWER prices — the buy/sell ratio and score kept
"confirming," but the price itself was already sliding down each time.
Confirming the wrong thing (activity) isn't the same as confirming the
right thing (the price is actually holding or rising).
"""

import time
import config

# In-memory tracker: address -> {"consecutive": int, "last_seen": float, "first_price": float}
# Intentionally NOT persisted to the state file — a restart resetting this
# is fine, it just means candidates need to re-confirm, which is safe
# (errs toward caution, never toward skipping a check).
_watch = {}


def record_and_check(address: str, qualifies_this_scan: bool, current_price: float = None) -> bool:
    """
    Call once per candidate per scan cycle. Returns True only once the
    candidate has qualified on config.MOMENTUM_CONFIRMATION_SCANS
    consecutive scans AND the price has not dropped since the first of
    those scans.
    """
    now = time.time()
    entry = _watch.get(address)

    if not qualifies_this_scan:
        if address in _watch:
            del _watch[address]
        return False

    if entry is None:
        _watch[address] = {"consecutive": 1, "last_seen": now, "first_price": current_price}
        return 1 >= config.MOMENTUM_CONFIRMATION_SCANS

    # NEW: reject if price has fallen since we first started watching this
    # candidate — activity/ratio still "qualifying" doesn't mean the price
    # itself is holding.
    if current_price is not None and entry.get("first_price"):
        if current_price < entry["first_price"]:
            del _watch[address]
            return False

    entry["consecutive"] += 1
    entry["last_seen"] = now
    return entry["consecutive"] >= config.MOMENTUM_CONFIRMATION_SCANS


def cleanup_stale(max_age_seconds: int = 600):
    """Drop candidates we haven't seen in a while (e.g. they fell out of
    the scan window) so the tracker doesn't grow unbounded."""
    now = time.time()
    stale = [addr for addr, e in _watch.items() if now - e["last_seen"] > max_age_seconds]
    for addr in stale:
        del _watch[addr]
