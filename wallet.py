"""
Wallet handling. Loads a private key from the SOLANA_PRIVATE_KEY environment
variable — NEVER hardcode a key in source, NEVER commit it to GitHub.

Uses a dedicated burner wallet, as discussed — only ever fund this with
money you're fully prepared to lose.
"""

import logging
import base58
import config

log = logging.getLogger("wallet")

_keypair = None


def get_keypair():
    global _keypair
    if _keypair is not None:
        return _keypair

    if not config.SOLANA_PRIVATE_KEY:
        raise RuntimeError(
            "SOLANA_PRIVATE_KEY environment variable is not set. "
            "Export your burner wallet's private key from Phantom "
            "(Settings -> Export Private Key) and set it as an env var. "
            "Never share this key or commit it anywhere."
        )

    try:
        from solders.keypair import Keypair
        secret_bytes = base58.b58decode(config.SOLANA_PRIVATE_KEY)
        _keypair = Keypair.from_bytes(secret_bytes)
        log.info(f"Wallet loaded: {_keypair.pubkey()}")
        return _keypair
    except Exception as e:
        raise RuntimeError(f"Failed to load wallet from SOLANA_PRIVATE_KEY: {e}")


def get_public_key_str():
    return str(get_keypair().pubkey())
