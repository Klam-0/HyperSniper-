"""
Scores a candidate pair using the same categories as the scorecard bot,
then combines with the independent security_checks safety verdict.
A token must pass BOTH the numeric score AND the safety check to qualify.
"""

import time
import logging
import config
from security_checks import evaluate_safety

log = logging.getLogger("scorer")


def score_liquidity(pair):
    liq = (pair.get("liquidity", {}) or {}).get("usd", 0) or 0
    pts = 2 if liq > 50000 else 1 if liq > config.MIN_LIQUIDITY_USD else 0
    return pts, liq


def score_momentum(pair):
    pts = 0
    txns = pair.get("txns", {}).get("m15", {}) or pair.get("txns", {}).get("h1", {}) or {}
    buys, sells = txns.get("buys", 0) or 0, txns.get("sells", 0) or 0
    ratio = buys / sells if sells > 0 else (buys if buys > 0 else 0)
    if sells > 0 and (buys / sells) >= config.MIN_BUY_SELL_RATIO_15M:
        pts += 2
    elif buys > sells:
        pts += 1

    vol = (pair.get("volume", {}) or {}).get("h24", 0) or 0
    mcap = pair.get("fdv", 0) or 0
    if mcap > 0:
        turnover = vol / mcap
        pts += 2 if turnover > 0.5 else 1 if turnover > 0.1 else 0

    return min(pts, 4), buys, sells


def score_timing(pair):
    created = pair.get("pairCreatedAt")
    if not created:
        return 0
    age_min = (time.time() * 1000 - created) / (1000 * 60)
    if config.MIN_AGE_MINUTES <= age_min <= config.MAX_AGE_MINUTES:
        return 2
    return 0


def check_pump_distance(pair):
    """NEW: rejects tokens that have already spiked hard in the last hour —
    a classic sign you'd be buying at/near the top of a pump, not the start.
    Returns (passes: bool, change_1h_pct: float)."""
    change_1h = (pair.get("priceChange", {}) or {}).get("h1")
    if change_1h is None:
        return True, None  # no data — don't block on missing info, but not a strong signal either
    change_1h = float(change_1h)
    return change_1h <= config.MAX_PRICE_CHANGE_1H_PCT, change_1h


def check_wash_trading(pair):
    """NEW: flags tokens whose 24h volume is implausibly large relative to
    their liquidity — a common signature of wash trading, where the same
    small pool of money loops through many trades to manufacture the
    appearance of activity and organic interest. A healthy token typically
    turns over some multiple of its liquidity in a day; an extreme ratio
    (e.g. 40x+) is a red flag rather than a sign of strength.
    Returns (passes: bool, ratio: float|None)."""
    liq = (pair.get("liquidity", {}) or {}).get("usd", 0) or 0
    vol = (pair.get("volume", {}) or {}).get("h24", 0) or 0
    if liq <= 0:
        return True, None  # can't compute — don't block on missing data
    ratio = vol / liq
    return ratio <= config.MAX_VOLUME_TO_LIQUIDITY_RATIO, ratio


def evaluate(pair):
    """
    Returns a dict:
      { qualifies: bool, total_score: int, reasons: [str], address: str }
    """
    address = pair.get("baseToken", {}).get("address")
    name = pair.get("baseToken", {}).get("name", "Unknown")
    symbol = pair.get("baseToken", {}).get("symbol", "?")

    liq_pts, liq_usd = score_liquidity(pair)
    mom_pts, buys, sells = score_momentum(pair)
    time_pts = score_timing(pair)
    pump_ok, change_1h = check_pump_distance(pair)
    wash_ok, vol_liq_ratio = check_wash_trading(pair)

    is_safe, safety_reasons, top_holder_pct = evaluate_safety(address)

    # Safety points folded in: 4 if fully clean, 0 if any red flag
    safety_pts = 4 if is_safe else 0
    total = liq_pts + mom_pts + time_pts + safety_pts

    reasons = [
        f"Liquidity: ${liq_usd:,.0f}",
        f"Buys/Sells: {buys}/{sells}",
    ]
    if change_1h is not None:
        reasons.append(f"1h price change: {change_1h:+.1f}%")
    reasons += safety_reasons

    if not pump_ok:
        reasons.append(f"⚠️ Rejected: already up {change_1h:.1f}% in 1h — likely past the entry point")
    if not wash_ok:
        reasons.append(f"⚠️ Rejected: volume is {vol_liq_ratio:.0f}x liquidity — possible wash trading")

    qualifies = (
        is_safe
        and total >= config.MIN_SCORE_TO_BUY
        and liq_usd >= config.MIN_LIQUIDITY_USD
        and pump_ok
        and wash_ok
    )

    return {
        "qualifies": qualifies,
        "total_score": total,
        "reasons": reasons,
        "address": address,
        "name": name,
        "symbol": symbol,
        "price_usd": float(pair.get("priceUsd", 0) or 0),
    }
