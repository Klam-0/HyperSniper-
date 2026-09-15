"""
Reads actual on-chain token balances via RPC, rather than trusting purely
in-memory tracking — protects against drift from fees, partial fills, or
restarts losing precise state.
"""

import logging
import requests
import config

log = logging.getLogger("balances")


def get_token_balance_base_units(owner_pubkey: str, mint: str) -> int:
    """Returns the raw (base-unit) balance of `mint` held by `owner_pubkey`.
    Returns 0 if no token account exists or on error (never trade blind on
    a balance we can't verify)."""
    try:
        resp = requests.post(
            config.RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    owner_pubkey,
                    {"mint": mint},
                    {"encoding": "jsonParsed"},
                ],
            },
            timeout=10,
        )
        resp.raise_for_status()
        accounts = resp.json().get("result", {}).get("value", [])
        if not accounts:
            return 0
        amount_str = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]
        return int(amount_str)
    except Exception as e:
        log.warning(f"Failed to fetch token balance: {e}")
        return 0


def get_sol_balance_lamports(owner_pubkey: str) -> int:
    try:
        resp = requests.post(
            config.RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [owner_pubkey]},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("result", {}).get("value", 0)
    except Exception as e:
        log.warning(f"Failed to fetch SOL balance: {e}")
        return 0
