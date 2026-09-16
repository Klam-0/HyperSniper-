"""
NEW module: tracks candidates across scan cycles so the bot requires
SUSTAINED qualification (config.MOMENTUM_CONFIRMATION_SCANS consecutive
scans) before buying, instead of acting on a single snapshot.

This directly targets the failure pattern from the first 3 simulated
trades: momentum that looked good for one 30-second check but was already
reversing by the next one.
"""

import time
import config

# In-memory tracker: address -> {"consecutive": int, "last_seen": float}
# Intentionally NOT persisted to the state file — a restart resetting this
# is fine, it just means candidates need to re-confirm, which is safe
# (errs toward caution, never toward skipping a check).
_watch = {}


def record_and_check(address: str, qualifies_this_scan: bool) -> bool:
    """
    Call once per candidate per scan cycle. Returns True only once the
    candidate has qualified on config.MOMENTUM_CONFIRMATION_SCANS
    consecutive scans (and is still qualifying now).
    """
    now = time.time()
    entry = _watch.get(address)

    if not qualifies_this_scan:
        # Doesn't qualify this scan — reset its streak entirely.
        if address in _watch:
            del _watch[address]
        return False

    if entry is None:
        _watch[address] = {"consecutive": 1, "last_seen": now}
        return 1 >= config.MOMENTUM_CONFIRMATION_SCANS

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
