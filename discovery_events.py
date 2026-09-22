"""Tiny shared module — a single event both mempool_listener and
wallet_watcher can set, so trader.py's scan loop can wake on whichever
discovery source fires first, without needing to wait on multiple
threading.Event objects at once (which isn't natively supported)."""

import threading

new_discovery = threading.Event()
