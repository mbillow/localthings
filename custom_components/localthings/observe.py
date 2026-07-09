"""Observe-mode (CoAP OBSERVE) support layered on top of StateCache.

Owns mode selection (push vs. poll), the write-settle guard (drops a
just-written href's incoming updates for a few seconds so a slow-to-settle
device doesn't revert an optimistic write), and missed-notification
detection. `smartthings_local`'s StateCache/DtlsCoapSession/ObserveRefreshTask
are an external pip dependency we don't own, so behavior that would
naturally live inside StateCache.apply_rep lives here instead, gating
whether apply_rep is called at all.
"""
from __future__ import annotations

import logging
import threading
import time

from smartthings_local.ocf.state_cache import StateCache

_LOGGER = logging.getLogger(__name__)

MODE_OBSERVE = 'observe'
MODE_POLL = 'poll'

DEFAULT_SETTLE_S = 4.0


class ObserveManager:
    """Per-device observe-mode state: mode, write-settle guard, and (later)
    subscription/staleness tracking. Pure sync logic — safe to call from
    any thread; callers on the event loop must still marshal any HA state
    push through `hass.add_job`, this class does not touch asyncio."""

    def __init__(self, cache: StateCache, logger: logging.Logger | None = None):
        self.cache = cache
        self.log = logger or _LOGGER
        self.mode = MODE_POLL
        self.last_mode_change_ts = time.monotonic()
        self.last_mode_change_wall = time.time()
        self._settle_until: dict[str, float] = {}
        self._settle_lock = threading.Lock()

    def mark_write_pending(self, href: str, settle_s: float = DEFAULT_SETTLE_S) -> None:
        with self._settle_lock:
            self._settle_until[href] = time.monotonic() + settle_s

    def _is_settling(self, href: str) -> bool:
        with self._settle_lock:
            until = self._settle_until.get(href)
            if until is None:
                return False
            if time.monotonic() >= until:
                del self._settle_until[href]
                return False
            return True

    def apply(self, href: str, rep: dict, source: str) -> bool:
        """Gate a StateCache.apply_rep call through the write-settle guard."""
        if self._is_settling(href):
            self.log.debug("dropping %s update for %s (settling)", source, href)
            return False
        return self.cache.apply_rep(href, rep, source=source)
