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

import cbor2

from smartthings_local.ocf.state_cache import StateCache

_LOGGER = logging.getLogger(__name__)

MODE_OBSERVE = 'observe'
MODE_POLL = 'poll'

DEFAULT_SETTLE_S = 4.0
GRACE_PERIOD_S = 15.0
SUCCESS_FRACTION = 0.8


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
        self.subscribed_hrefs: set[str] = set()
        self._notified: set[str] = set()
        self.fallback_hrefs: set[str] = set()

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

    def on_notification(self, href: str, payload: bytes) -> None:
        """Wired as DtlsCoapSession.on_notification. Runs on the DTLS
        reader thread — must not touch asyncio."""
        try:
            rep = cbor2.loads(payload)
        except Exception as e:
            self.log.warning("observe %s: cbor decode failed: %s", href, e)
            return
        if not isinstance(rep, dict):
            return
        self._notified.add(href)
        self.apply(href, rep, source='observe')

    def try_enter_observe_mode(
        self, session, hrefs: list[str],
        grace_period_s: float = GRACE_PERIOD_S,
        success_fraction: float = SUCCESS_FRACTION,
    ) -> bool:
        """Blocking — subscribes to every href then sleeps for the whole
        grace period. Caller must run this in an executor, never on the
        event loop."""
        self._notified.clear()
        subscribed: set[str] = set()
        for href in hrefs:
            segs = [s for s in href.strip('/').split('/') if s]
            try:
                session.subscribe(segs)
                subscribed.add(href)
            except Exception as e:
                self.log.warning("subscribe %s failed: %s", href, e)
        if not subscribed:
            self._set_mode(MODE_POLL)
            self.subscribed_hrefs = set()
            return False

        time.sleep(grace_period_s)

        fraction = len(self._notified & subscribed) / len(subscribed)
        if fraction >= success_fraction:
            self.subscribed_hrefs = subscribed
            self._set_mode(MODE_OBSERVE)
            return True

        self.subscribed_hrefs = set()
        self._set_mode(MODE_POLL)
        return False

    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.log.info("observe-mode transition: %s -> %s", self.mode, mode)
            self.mode = mode
            self.last_mode_change_ts = time.monotonic()
            self.last_mode_change_wall = time.time()

    def check_sweep_for_misses(self, sweep_resources: dict[str, dict]) -> bool:
        """Compare a safety-net sweep result against the cache for every
        subscribed href. A mismatch outside its settle window means
        OBSERVE silently missed a change (it only notifies on change, so
        any disagreement is a real miss, not a case of "nothing changed
        yet"). Returns True if a miss was detected."""
        if self.mode != MODE_OBSERVE:
            return False
        for href in self.subscribed_hrefs:
            if href not in sweep_resources or self._is_settling(href):
                continue
            cached = self.cache.get(href)
            if cached is not None and cached != sweep_resources[href]:
                self.log.warning(
                    "observe missed a change on %s (sweep disagrees with cache)",
                    href,
                )
                return True
        return False

    def downgrade_to_poll(self) -> None:
        self.fallback_hrefs = set(self.subscribed_hrefs)
        self.subscribed_hrefs = set()
        self._set_mode(MODE_POLL)
