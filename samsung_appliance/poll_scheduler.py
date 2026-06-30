"""Tiered adaptive polling against a DtlsCoapSession.

Tiers are descriptor-declared (hot/warm/cold + sweep). Per tick:
each tier whose deadline has passed polls all its paths sequentially
on the shared session, writing into the StateCache. The sweep tier
issues one Block2 GET of /device/0 and uses index_links to fan its
result into many href reps.

Adaptive cadence: when descriptor.is_active(cache.links) returns True
and tier.active_interval_s is set, that tier uses the tighter cadence.
If the previous health window saw `active_throttle_threshold` timeouts,
the throttle drops back to idle cadence even when active=True — the
RT-OCF stack wedges under load and stacking poll attempts only makes
it worse.

Per-tier timeouts: tier.timeout_s overrides the scheduler default. Hot
tiers want a tight ceiling (e.g. 2s) so one wedged path can't eat
several poll cycles.

Cooldown on timeout: when a path times out, it's deferred for ~3 of
its tier's intervals (clamped 5–60s) via the same _defer_until mechanism
used for write_in_progress. Breaks the cluster-cascade where the next
tier tick fires immediately after an 8s wedge and reattempts the same
stalled path.

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
    # Per-tier CoAP request timeout. Falls back to PollScheduler.timeout_s
    # when None. Hot tiers want a tight ceiling (e.g. 2s) so one wedged
    # path can't eat several poll cycles; sweep tiers tolerate longer.
    timeout_s: Optional[float] = None


class PollScheduler:

    def __init__(self,
                 session: DtlsCoapSession,
                 cache: 'StateCache',
                 tiers: list[PollTier],
                 sweep_index_fn: Callable[[object], dict[str, dict]],
                 is_active_fn: Optional[Callable[[dict[str, dict]], bool]] = None,
                 logger=None,
                 timeout_s: float = 8.0,
                 active_throttle_timeout_threshold: int = 3):
        self.session = session
        self.cache = cache
        self.tiers = tiers
        self.sweep_index = sweep_index_fn
        self.is_active_fn = is_active_fn
        self.log = logger
        self.timeout_s = timeout_s
        self.active_throttle_threshold = active_throttle_timeout_threshold

        now = time.monotonic()
        self._next_due: dict[str, float] = {t.name: now for t in tiers}
        self._defer_until: dict[str, float] = {}
        self._defer_lock = threading.Lock()
        self._poll_count = 0
        self._poll_error_count = 0
        self._last_active: Optional[bool] = None
        self._last_throttled: bool = False
        # Real-liveness signal for KeepaliveTask: updated on every 2.05
        # we receive from the wire. Initialized to "now" so the first
        # keepalive tick after start doesn't fire a false unreachable.
        self._last_success_ts: float = now

        # Per-window tail-latency tracking. Bridge consumes-and-resets
        # these via take_window_stats() once per HEALTH_INTERVAL_S.
        # _window_max_rtt_ms tracks SUCCESSFUL polls only — timeouts go
        # into _window_timeout_count so the dashboard sees the real tail
        # instead of an 8000ms wall.
        self._stats_lock = threading.Lock()
        self._window_max_rtt_ms = 0.0
        self._window_slow_count = 0
        self._window_timeout_count = 0
        # Snapshot of the previous window's timeout count, used by the
        # active-window throttle to back off when polls are wedging.
        self._last_window_timeouts = 0
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

    @property
    def last_success_ts(self) -> float:
        """Monotonic timestamp of the most recent 2.05 response from any
        tier. KeepaliveTask uses this as its half-open-detection signal:
        if no 2.05 has landed in `liveness_window_s`, the session is
        wedged regardless of whether ping sends succeed."""
        return self._last_success_ts

    def take_window_stats(self) -> tuple[float, int, int]:
        """Return (max RTT ms over successful polls, slow-poll count,
        timeout count) seen since the last call, and reset all three.
        Slow threshold is `self.slow_threshold_ms`. The timeout count
        is snapshotted into `_last_window_timeouts` for the throttle."""
        with self._stats_lock:
            out = (self._window_max_rtt_ms,
                   self._window_slow_count,
                   self._window_timeout_count)
            self._last_window_timeouts = self._window_timeout_count
            self._window_max_rtt_ms = 0.0
            self._window_slow_count = 0
            self._window_timeout_count = 0
        return out

    def _record_rtt(self, rtt_ms: float, *, timed_out: bool = False) -> None:
        with self._stats_lock:
            if timed_out:
                self._window_timeout_count += 1
                return
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
        # Active-window throttle: if the previous health window saw a
        # cluster of timeouts (RT-OCF wedging under load), drop back to
        # idle cadence even when is_active=True. Lets the device breathe
        # instead of stacking poll attempts on a stalled responder.
        with self._stats_lock:
            recent_to = self._last_window_timeouts
        throttled = (active
                     and recent_to >= self.active_throttle_threshold)
        if throttled != self._last_throttled:
            if self.log:
                if throttled:
                    self.log.warning(
                        "active-throttle ON (%d timeouts last window) — "
                        "using idle cadence", recent_to)
                else:
                    self.log.info("active-throttle OFF")
            self._last_throttled = throttled
        effective_active = active and not throttled
        for tier in self.tiers:
            if self._next_due[tier.name] > now:
                continue
            interval = (tier.active_interval_s
                        if (effective_active and tier.active_interval_s is not None)
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

    def _tier_timeout(self, tier: PollTier) -> float:
        return tier.timeout_s if tier.timeout_s is not None else self.timeout_s

    def _cooldown_for(self, tier: PollTier) -> float:
        # On timeout, defer the wedged href for ~3 cycles (clamped to a
        # 5–60s band) so repeated tier ticks don't stack attempts on a
        # stalled responder. Breaks the cluster-cascade we see in the
        # Poll Max RTT chart during heavy device use.
        return max(5.0, min(60.0, tier.interval_s * 3.0))

    def _set_cooldown(self, href: str, cooldown_s: float) -> None:
        with self._defer_lock:
            self._defer_until[href] = time.monotonic() + cooldown_s

    def _do_tier(self, tier: PollTier) -> None:
        timeout = self._tier_timeout(tier)
        cooldown = self._cooldown_for(tier)
        for path in tier.paths:
            href = '/' + '/'.join(path)
            with self._defer_lock:
                if self._defer_until.get(href, 0) > time.monotonic():
                    continue
            self._poll_count += 1
            t0 = time.monotonic()
            try:
                code, body = self.session.get(list(path), timeout=timeout)
            except TimeoutError:
                self._poll_error_count += 1
                self._record_rtt(0.0, timed_out=True)
                self._set_cooldown(href, cooldown)
                if self.log:
                    self.log.warning("poll %s timeout (cooldown %.0fs)",
                                     href, cooldown)
                return
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
            self._last_success_ts = time.monotonic()
            try:
                rep = cbor2.loads(body)
            except Exception as e:
                self._poll_error_count += 1
                if self.log: self.log.warning("poll %s cbor: %s", href, e)
                continue
            if isinstance(rep, dict):
                self.cache.apply_rep(href, rep, source='poll')

    def _do_sweep(self, tier: PollTier) -> None:
        timeout = self._tier_timeout(tier)
        cooldown = self._cooldown_for(tier)
        path = list(tier.paths[0])
        href = '/' + '/'.join(path)
        t0 = time.monotonic()
        self._poll_count += 1
        try:
            code, body = self.session.get(path, timeout=timeout)
        except TimeoutError:
            self._poll_error_count += 1
            self._record_rtt(0.0, timed_out=True)
            self._set_cooldown(href, cooldown)
            if self.log:
                self.log.warning("sweep %s timeout (cooldown %.0fs)",
                                 path, cooldown)
            return
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
        self._last_success_ts = time.monotonic()
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
