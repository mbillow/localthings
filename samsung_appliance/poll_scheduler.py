"""Tiered adaptive polling against a DtlsCoapSession.

Tiers are descriptor-declared (hot/warm/cold + sweep). Per tick:
each tier whose deadline has passed polls all its paths sequentially
on the shared session, writing into the StateCache. The sweep tier
issues one Block2 GET of /device/0 and uses index_links to fan its
result into many href reps.

Adaptive cadence: when descriptor.is_active(cache.links) returns True
and tier.active_interval_s is set, that tier uses the tighter cadence.

Post-write defer: bridge calls write_in_progress(href) before POSTing
a write; the scheduler skips that href for settle_s to avoid Samsung's
fetchback-revert bug.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

import cbor2

from .coap_dtls import DtlsCoapSession, fmt_code

if TYPE_CHECKING:
    from .state_cache import StateCache


@dataclass(frozen=True)
class PollTier:
    name: str
    interval_s: float
    paths: tuple[tuple[str, ...], ...]
    active_interval_s: Optional[float] = None
    is_sweep: bool = False


class PollScheduler:

    def __init__(self,
                 session: DtlsCoapSession,
                 cache: 'StateCache',
                 tiers: list[PollTier],
                 sweep_index_fn: Callable[[object], dict[str, dict]],
                 is_active_fn: Optional[Callable[[dict[str, dict]], bool]] = None,
                 logger=None,
                 timeout_s: float = 8.0):
        self.session = session
        self.cache = cache
        self.tiers = tiers
        self.sweep_index = sweep_index_fn
        self.is_active_fn = is_active_fn
        self.log = logger
        self.timeout_s = timeout_s

        now = time.monotonic()
        self._next_due: dict[str, float] = {t.name: now for t in tiers}
        self._defer_until: dict[str, float] = {}
        self._defer_lock = threading.Lock()
        self._poll_count = 0
        self._poll_error_count = 0
        self._last_active: Optional[bool] = None

        # Per-window tail-latency tracking. Bridge consumes-and-resets
        # these via take_window_stats() once per HEALTH_INTERVAL_S.
        self._stats_lock = threading.Lock()
        self._window_max_rtt_ms = 0.0
        self._window_slow_count = 0
        self.slow_threshold_ms = 1000.0

    def write_in_progress(self, href: str, settle_s: float = 4.0) -> None:
        with self._defer_lock:
            self._defer_until[href] = time.monotonic() + settle_s

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._run_due_tiers()
            sleep_for = max(0.05, min(1.0, self._earliest_deadline() - time.monotonic()))
            if stop.wait(sleep_for):
                return

    @property
    def poll_count(self) -> int:
        return self._poll_count

    @property
    def poll_error_count(self) -> int:
        return self._poll_error_count

    def take_window_stats(self) -> tuple[float, int]:
        """Return (max RTT ms, slow-poll count) seen since the last call,
        and reset both. Slow threshold is `self.slow_threshold_ms`."""
        with self._stats_lock:
            out = (self._window_max_rtt_ms, self._window_slow_count)
            self._window_max_rtt_ms = 0.0
            self._window_slow_count = 0
        return out

    def _record_rtt(self, rtt_ms: float) -> None:
        with self._stats_lock:
            if rtt_ms > self._window_max_rtt_ms:
                self._window_max_rtt_ms = rtt_ms
            if rtt_ms >= self.slow_threshold_ms:
                self._window_slow_count += 1

    def _earliest_deadline(self) -> float:
        return min(self._next_due.values())

    def _run_due_tiers(self) -> None:
        now = time.monotonic()
        active = False
        if self.is_active_fn is not None:
            try:
                active = bool(self.is_active_fn(self.cache.snapshot()))
            except Exception as e:
                if self.log: self.log.warning("is_active: %s", e)
        if active != self._last_active:
            if self.log and self._last_active is not None:
                self.log.info("active=%s", active)
            self._last_active = active
        for tier in self.tiers:
            if self._next_due[tier.name] > now:
                continue
            interval = (tier.active_interval_s
                        if (active and tier.active_interval_s is not None)
                        else tier.interval_s)
            self._next_due[tier.name] = now + interval
            try:
                if tier.is_sweep:
                    self._do_sweep(tier)
                else:
                    self._do_tier(tier)
            except Exception as e:
                self._poll_error_count += 1
                if self.log: self.log.warning("tier %s: %s", tier.name, e)

    def _do_tier(self, tier: PollTier) -> None:
        for path in tier.paths:
            href = '/' + '/'.join(path)
            with self._defer_lock:
                if self._defer_until.get(href, 0) > time.monotonic():
                    continue
            self._poll_count += 1
            t0 = time.monotonic()
            try:
                code, body = self.session.get(list(path), timeout=self.timeout_s)
            except Exception as e:
                self._poll_error_count += 1
                self._record_rtt((time.monotonic() - t0) * 1000.0)
                if self.log: self.log.warning("poll %s: %s", href, e)
                return
            self._record_rtt((time.monotonic() - t0) * 1000.0)
            if code != 0x45 or not body:
                self._poll_error_count += 1
                if self.log: self.log.warning("poll %s -> %s", href, fmt_code(code))
                continue
            try:
                rep = cbor2.loads(body)
            except Exception as e:
                self._poll_error_count += 1
                if self.log: self.log.warning("poll %s cbor: %s", href, e)
                continue
            if isinstance(rep, dict):
                self.cache.apply_rep(href, rep, source='poll')

    def _do_sweep(self, tier: PollTier) -> None:
        path = list(tier.paths[0])
        t0 = time.monotonic()
        self._poll_count += 1
        try:
            code, body = self.session.get(path, timeout=self.timeout_s)
        except Exception as e:
            self._poll_error_count += 1
            self._record_rtt((time.monotonic() - t0) * 1000.0)
            if self.log: self.log.warning("sweep %s: %s", path, e)
            return
        self._record_rtt((time.monotonic() - t0) * 1000.0)
        if code != 0x45 or not body:
            self._poll_error_count += 1
            if self.log: self.log.warning("sweep -> %s", fmt_code(code))
            return
        try:
            tree = cbor2.loads(body)
        except Exception as e:
            self._poll_error_count += 1
            if self.log: self.log.warning("sweep cbor: %s", e)
            return
        indexed = self.sweep_index(tree)
        for href, rep in indexed.items():
            with self._defer_lock:
                if self._defer_until.get(href, 0) > time.monotonic():
                    continue
            self.cache.apply_rep(href, rep, source='sweep')
        if self.log:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self.log.info("sweep complete (%d links, %.0fms)",
                          len(indexed), elapsed_ms)
