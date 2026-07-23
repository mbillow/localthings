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
from smartthings_local.ocf.observe_refresh import ObserveRefreshTask

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 6 * 3600.0

MODE_OBSERVE = 'observe'
MODE_POLL = 'poll'

DEFAULT_SETTLE_S = 4.0
GRACE_PERIOD_S = 15.0


def _rep_diff(cached: dict, sweep: dict) -> dict:
    """Fields present in BOTH reps whose values differ, as
    {field: (cached_value, sweep_value)}.

    The batch `/device/0` sweep and an individual GET/notify can return
    genuinely different-shaped reps for the same href (e.g. Samsung's
    batch interface omits `rt`/`if` baseline fields that a direct GET
    includes) — that's a representation-shape difference, not a missed
    state change. Only comparing shared fields keeps the diff (and the
    caller's miss detection) about real content changes.
    """
    common = set(cached) & set(sweep)
    return {k: (cached[k], sweep[k]) for k in common if cached[k] != sweep[k]}


SUCCESS_FRACTION = 0.8

# A notify within this long counts as proof the DTLS session is alive,
# even if the summary poll's blockwise GET just timed out on a slow
# device. Twice the coordinator's 30s summary interval: generous enough
# to absorb jitter, tight enough that a channel that's actually gone
# quiet doesn't get credit for a notify from a while ago.
PUSH_HEALTH_WINDOW_S = 60.0


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
        self._last_notify_ts: float | None = None
        self.fallback_hrefs: set[str] = set()
        self._refresh_task: ObserveRefreshTask | None = None
        self._refresh_stop: threading.Event | None = None
        self._refresh_thread: threading.Thread | None = None

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
        """Gate a StateCache.apply_rep call through the write-settle guard.

        Merges the incoming rep onto whatever's already cached for this
        href rather than handing it to StateCache.apply_rep verbatim --
        apply_rep does a full replace, and Samsung devices don't always
        repeat every field on every update (issue #27: a /mode/vs/0
        OBSERVE notify -- and, on at least one Bespoke fridge, even the
        /device/0 sweep entry for that href -- can carry just `modes`,
        omitting `supportedOptions`/`supportedModes` entirely). A full
        replace would silently wipe fields a select entity's
        exists_fn/options_field gates on the moment one partial update
        comes through, even though nothing about the device's actual
        supported options changed.
        """
        if self._is_settling(href):
            self.log.debug("dropping %s update for %s (settling)", source, href)
            return False
        prior = self.cache.get(href)
        merged = {**prior, **rep} if prior else rep
        return self.cache.apply_rep(href, merged, source=source)

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
        self._last_notify_ts = time.monotonic()
        self.log.debug("observe notify: %s", href)
        self.apply(href, rep, source='observe')

    def recently_notified(self, window_s: float = PUSH_HEALTH_WINDOW_S) -> bool:
        """True if any OBSERVE notify has arrived within `window_s`.

        Used only to gate whether a poll (summary GET) failure should
        close/reconnect the DTLS session — NOT to decide observe-mode
        health in general (see `log_sweep_discrepancies` for why that
        distinction matters: a notify recent enough to prove the session
        itself is alive is a much stronger, narrower claim than "the
        channel has been perfectly healthy").
        """
        return (
            self._last_notify_ts is not None
            and time.monotonic() - self._last_notify_ts < window_s
        )

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
            self._stop_refresh_task()
            self._set_mode(MODE_POLL)
            self.subscribed_hrefs = set()
            return False

        time.sleep(grace_period_s)

        fraction = len(set(self._notified) & subscribed) / len(subscribed)
        if fraction >= success_fraction:
            self.subscribed_hrefs = subscribed
            self._set_mode(MODE_OBSERVE)
            self.start_refresh_task(session)
            return True

        self._stop_refresh_task()
        self.subscribed_hrefs = set()
        self._set_mode(MODE_POLL)
        return False

    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.log.info("observe-mode transition: %s -> %s", self.mode, mode)
            self.mode = mode
            self.last_mode_change_ts = time.monotonic()
            self.last_mode_change_wall = time.time()

    def log_sweep_discrepancies(self, sweep_resources: dict[str, dict]) -> bool:
        """Log any subscribed href where the safety-net sweep disagrees
        with the cache, on fields present in both reps (see `_rep_diff`
        for why only shared fields count). Returns True if any
        discrepancy was found (False if not in observe mode).

        This never changes mode or subscriptions. The sweep already
        re-applies the full /device/0 result to the cache every cycle
        regardless of mode, so a missed notify never leaves data stale
        beyond one sweep interval — there's nothing to correct here. And
        a still-live OBSERVE session should never be torn down over a
        data-drift inference: some resources (e.g. an alarm's derived
        "triggeredTime") appear to update without ever emitting a notify
        even when the channel is otherwise perfectly healthy, so treating
        a mismatch as proof of channel death produces false positives.
        Downgrading also throws away working push coverage for every
        *other* subscribed href just to react to one that isn't the
        problem.

        The return value lets the caller respond more cheaply: without
        an activity signal there's no way to tell "idle device, healthy
        channel, nothing to notify about" from "dead channel, nothing
        gets through" — both look the same here. So instead of guessing,
        the coordinator uses a discrepancy as a trigger for extra hot/warm
        subpolls this cycle (see `_run_subpolls`'s `force` parameter) —
        a bounded, self-limiting response to a channel that's gone silent
        for a reason OTHER than a reconnect (e.g. the device's OBSERVE
        relay loses its internet connection while the local DTLS session
        stays up), without tearing down subscriptions that will recover
        on their own once notifies resume.
        """
        if self.mode != MODE_OBSERVE:
            return False
        found = False
        for href in self.subscribed_hrefs:
            if href not in sweep_resources or self._is_settling(href):
                continue
            cached = self.cache.get(href)
            if cached is None:
                continue
            diff = _rep_diff(cached, sweep_resources[href])
            if diff:
                found = True
                self.log.debug(
                    "observe missed a change on %s (sweep disagrees with cache): %s",
                    href, diff,
                )
        return found

    def downgrade_to_poll(self) -> None:
        self.fallback_hrefs = set(self.subscribed_hrefs)
        self.subscribed_hrefs = set()
        self._stop_refresh_task()
        self._set_mode(MODE_POLL)

    def start_refresh_task(self, session) -> None:
        self._stop_refresh_task()
        paths = [tuple(h.strip('/').split('/')) for h in self.subscribed_hrefs]
        self._refresh_task = ObserveRefreshTask(
            session, paths, interval_s=REFRESH_INTERVAL_S, logger=self.log,
        )
        self._refresh_stop = threading.Event()
        self._refresh_thread = threading.Thread(
            target=self._refresh_task.run_forever,
            args=(self._refresh_stop,),
            daemon=True, name='localthings-observe-refresh',
        )
        self._refresh_thread.start()

    def _stop_refresh_task(self) -> None:
        if self._refresh_stop is not None:
            self._refresh_stop.set()
        self._refresh_thread = None
        self._refresh_task = None
        self._refresh_stop = None

    def close(self) -> None:
        self._stop_refresh_task()
