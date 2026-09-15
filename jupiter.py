"""
Executes buy/sell swaps via the Jupiter Aggregator API — the standard
DEX router on Solana, used by virtually every trading bot mentioned
earlier in this project (BonkBot, Trojan, GMGN, etc all route through it
or something equivalent).

Respects config.DRY_RUN: in dry-run mode, quotes are fetched (so scoring
and logging reflect real prices) but no transaction is ever signed or sent.
"""

import base64
import logging
import time
import requests
import config
from wallet import get_keypair

log = logging.getLogger("jupiter")


def get_quote(input_mint: str, output_mint: str, amount_lamports: int, slippage_bps: int = None):
    slippage_bps = slippage_bps or config.MAX_SLIPPAGE_BPS
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount_lamports,
        "slippageBps": slippage_bps,
    }
    r = requests.get(config.JUPITER_QUOTE_URL, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _send_swap_transaction(quote: dict):
    """Builds, signs, and sends the actual swap transaction. Only called
    when DRY_RUN is False."""
    from solders.transaction import VersionedTransaction
    from solders.keypair import Keypair

    keypair: Keypair = get_keypair()

    swap_resp = requests.post(
        config.JUPITER_SWAP_URL,
        json={
            "quoteResponse": quote,
            "userPublicKey": str(keypair.pubkey()),
            "wrapAndUnwrapSol": True,
            "prioritizationFeeLamports": "auto",
        },
        timeout=15,
    )
    swap_resp.raise_for_status()
    swap_tx_b64 = swap_resp.json()["swapTransaction"]

    raw_tx = base64.b64decode(swap_tx_b64)
    tx = VersionedTransaction.from_bytes(raw_tx)
    signed_tx = VersionedTransaction(tx.message, [keypair])
    signed_b64 = base64.b64encode(bytes(signed_tx)).decode("utf-8")

    rpc_resp = requests.post(
        config.RPC_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [signed_b64, {"encoding": "base64", "skipPreflight": False, "maxRetries": 3}],
        },
        timeout=20,
    )
    rpc_resp.raise_for_status()
    result = rpc_resp.json()
    if "error" in result:
        raise RuntimeError(f"RPC rejected transaction: {result['error']}")

    signature = result["result"]
    log.info(f"Transaction sent: {signature}")
    return signature


def confirm_transaction(signature: str, timeout_seconds: int = 30):
    """Polls the RPC for confirmation. Returns True/False."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            resp = requests.post(
                config.RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignatureStatuses",
                    "params": [[signature]],
                },
                timeout=10,
            )
            statuses = resp.json().get("result", {}).get("value", [None])
            if statuses[0] and statuses[0].get("confirmationStatus") in ("confirmed", "finalized"):
                return True
        except Exception as e:
            log.warning(f"Confirmation check failed: {e}")
        time.sleep(2)
    return False


def swap(input_mint: str, output_mint: str, amount_lamports: int, slippage_bps: int = None):
    """
    Full swap flow. Returns:
      { success: bool, signature: str|None, quote: dict, simulated: bool, error: str|None }
    """
    try:
        quote = get_quote(input_mint, output_mint, amount_lamports, slippage_bps)
    except Exception as e:
        return {"success": False, "signature": None, "quote": None, "simulated": config.DRY_RUN, "error": str(e)}

    if config.DRY_RUN:
        log.info(f"[DRY RUN] Would swap {amount_lamports} of {input_mint} -> {output_mint}")
        return {"success": True, "signature": None, "quote": quote, "simulated": True, "error": None}

    try:
        signature = _send_swap_transaction(quote)
        confirmed = confirm_transaction(signature)
        if not confirmed:
            return {
                "success": False,
                "signature": signature,
                "quote": quote,
                "simulated": False,
                "error": "Transaction sent but not confirmed within timeout — check manually",
            }
        return {"success": True, "signature": signature, "quote": quote, "simulated": False, "error": None}
    except Exception as e:
        log.error(f"Swap failed: {e}")
        return {"success": False, "signature": None, "quote": quote, "simulated": False, "error": str(e)}
