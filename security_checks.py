"""
Safety layer: combines GoPlus Security and RugCheck.xyz so no single
data source's blind spot gets a token waved through.
"""

import logging
import requests
import config

log = logging.getLogger("security")


def fetch_goplus_solana(address: str):
    try:
        r = requests.get(
            config.GOPLUS_SOLANA_URL,
            params={"contract_addresses": address},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json().get("result", {})
        return result.get(address)
    except Exception as e:
        log.warning(f"GoPlus lookup failed: {e}")
        return None


def fetch_rugcheck(address: str):
    """RugCheck gives a second, independent opinion — Solana-native scoring
    plus LP-lock verification. Treat a failure here as 'unknown', not 'safe'."""
    try:
        r = requests.get(f"{config.RUGCHECK_BASE}/tokens/{address}/report", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"RugCheck lookup failed: {e}")
        return None


def evaluate_safety(address: str):
    """
    Returns (is_safe: bool, reasons: list[str], top_holder_pct: float|None)
    is_safe requires BOTH sources to either agree it's safe, or be silent
    (never overrides a red flag from either source).
    """
    reasons = []
    is_safe = True
    top_holder_pct = None

    goplus = fetch_goplus_solana(address)
    if goplus:
        mintable = goplus.get("mintable", {}) or {}
        freezable = goplus.get("freezable", {}) or {}
        if str(freezable.get("status")) == "1":
            is_safe = False
            reasons.append("GoPlus: freeze authority still active")
        if str(mintable.get("status")) == "1":
            reasons.append("GoPlus: mint authority still active (soft flag)")

        holders = goplus.get("holders", [])
        if holders:
            top_holder_pct = max(float(h.get("percent", 0)) for h in holders) * 100
            if top_holder_pct > config.MAX_TOP_HOLDER_PCT:
                is_safe = False
                reasons.append(f"GoPlus: top holder owns {top_holder_pct:.1f}% of supply")
    else:
        reasons.append("GoPlus: no data available (treated as unverified, not unsafe)")

    rugcheck = fetch_rugcheck(address)
    if rugcheck:
        score = rugcheck.get("score")  # lower is generally better on RugCheck's scale
        risks = rugcheck.get("risks", []) or []
        danger_risks = [r for r in risks if r.get("level") in ("danger", "high")]
        if danger_risks:
            is_safe = False
            for r in danger_risks:
                reasons.append(f"RugCheck: {r.get('name', 'flagged risk')}")
    else:
        reasons.append("RugCheck: no data available (treated as unverified, not unsafe)")

    # If BOTH sources returned nothing, we have no safety signal at all —
    # don't trade blind.
    if not goplus and not rugcheck:
        is_safe = False
        reasons.append("No safety data from any source — refusing to trade blind")

    return is_safe, reasons, top_holder_pct
