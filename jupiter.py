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


def _jupiter_headers():
    if config.JUPITER_API_KEY:
        return {"x-api-key": config.JUPITER_API_KEY}
    return {}


def get_quote(input_mint: str, output_mint: str, amount_lamports: int, slippage_bps: int = None):
    slippage_bps = slippage_bps or config.MAX_SLIPPAGE_BPS
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount_lamports,
        "slippageBps": slippage_bps,
