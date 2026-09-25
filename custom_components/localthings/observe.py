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
from collections.abc import Callable

import cbor2
from smartthings_local.ocf.observe_refresh import ObserveRefreshTask
from smartthings_local.ocf.state_cache import StateCache

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL_S = 6 * 3600.0

MODE_OBSERVE = "observe"
MODE_POLL = "poll"

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


def _is_alarms_href(href: str) -> bool:
    """True for /alarms/vs/<index> in any subdevice-translated shape --
    the canonical MAIN form (/alarms/vs/0), an indexed subdevice's
    renumbered instance (/alarms/vs/<key>), or a prefixed subdevice's
    UUID-qualified form (/<uuid>/alarms/vs/0). `Subdevice.to_actual`
    (registry/subdevices.py) only ever rewrites the trailing index
    segment or prepends a prefix -- it never touches the 'alarms/vs'
    stem -- so matching that fixed segment plus a wildcard tail catches
    every shape without this module needing to be subdevice-aware.

    See `ObserveManager.apply`'s use of this for why the href matters:
    unlike most resources, /alarms/vs/0's `x.com.samsung.da.items` array
    is a complete snapshot of every currently-active alarm, not a
    possibly-partial field update -- so it must never be merged onto a
    stale prior rep (issue #348).
    """
    head, _, _ = href.rpartition("/")
    return head.endswith("/alarms/vs")


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
        self._cache_lock = threading.Lock()
        self.subscribed_hrefs: set[str] = set()
        self._notified: set[str] = set()
        self._last_notify_ts: float | None = None
        # Wakes try_enter_observe_mode's grace wait early once enough hrefs
        # have notified. Guards `_notified` mutations, the `wait_for`, and
        # fallback_hrefs (enter_observe_mode assignment, on_notification
        # discard).
        self._notify_cond = threading.Condition()
        # Idle while polling, except after downgrade_to_poll (every href
        # that was subscribed). While in observe mode this is the set of
        # subscribed hrefs that have not yet notified (issue #92) -- they
        # stay on the hot/warm sub-poll cadence. on_notification discards
        # an href once it pushes, so a late first notify self-corrects.
        self.fallback_hrefs: set[str] = set()
        self._on_applied: Callable[[str, dict, str], None] | None = None
        self._refresh_task: ObserveRefreshTask | None = None
        self._refresh_stop: threading.Event | None = None
        self._refresh_thread: threading.Thread | None = None

    def set_on_applied(self, callback: Callable[[str, dict, str], None]) -> None:
        """Hook run after every accepted rep, on the applying thread.

        Unlike StateCache.set_on_change it carries the href and rep, and
        fires even when the rep is unchanged -- which learned.py needs, a
        device sitting in an unadvertised mode re-sending the same rep
        every poll.
        """
        self._on_applied = callback

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

        `_is_alarms_href` is the one exception to that merge (issue #348):
        /alarms/vs/0's `items` array is always sent as a complete
        snapshot of every currently-active alarm, never a partial delta
        -- confirmed by a live `read_resource` GET returning `{}` (no
        `items` key at all) the moment a washer's board actually clears
        an alarm, which entity.py already documents as this resource's
        normal no-alarm shape. Merging that `{}` onto the prior rep the
        same way as everywhere else silently kept the stale `items`
        entry forever: an absent key merges as "unchanged" everywhere
        else, but on this href absent specifically means "cleared".
        Every family that exposes an alarm sensor shares this href
        (common.ALARMS, range_hood's own copy), so this is a full
        replace for all of them, not a washer-specific carve-out.

        `apply()` is the sole path StateCache mutations flow through in
        this component (poll, sweep, and OBSERVE notify all funnel here),
        so `_cache_lock` serializes the read-then-write across those
        threads (DTLS reader for notifies, executor threads for poll/
        sweep) -- without it, two concurrent updates for the same href
        could each read the same prior rep and the second writer would
        silently lose the first's fields, reintroducing the exact bug
        this merge fixes.

        `source == 'optimistic'` always bypasses the settle gate below,
        even while this href is already settling from an earlier write.
        Several independent selects can share one href -- issue #9's washer
        packs cycle, detergent, and softener settings onto the same
        /course/vs/0 -- so a second, different write to the same href
        while the first's settle window is still open (up to
        _POST_TIMEOUT_S + _POLL_TIMEOUT_S, tens of seconds -- see
        coordinator.async_send_command) is an expected, normal sequence,
        not a stale echo of the first write. Gating it the same as a
        poll/sweep/observe update would silently drop the user's own
        second selection from the cache until the first write's guard
        happened to expire, i.e. the exact symptom this guard exists to
        prevent, just relocated to whichever write loses the race.
        """
        if source != "optimistic" and self._is_settling(href):
            self.log.debug("dropping %s update for %s (settling)", source, href)
            return False
        with self._cache_lock:
            merged = dict(rep) if _is_alarms_href(href) else {**(self.cache.get(href) or {}), **rep}
            changed = self.cache.apply_rep(href, merged, source=source)
        # Outside the cache lock -- the hook takes locks of its own and
        # never reads the cache back. `source` is passed along rather than
        # filtered here: which sources are worth acting on is the hook's
        # policy, not this manager's.
        if self._on_applied is not None:
            self._on_applied(href, merged, source)
        return changed

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
        if self._resource_type_mismatch(href, rep):
            return
        with self._notify_cond:
            self._notified.add(href)
            # Snapshot in enter_observe_mode is at the 80% quorum, not the
            # full grace period -- a late first push (blockwise refetch)
            # must drop the href so we don't keep GET-polling a live one.
            self.fallback_hrefs.discard(href)
            self._last_notify_ts = time.monotonic()
            self._notify_cond.notify_all()
        self.log.debug("observe notify: %s", href)
        self.apply(href, rep, source="observe")

    def _resource_type_mismatch(self, href: str, rep: dict) -> bool:
        """True when a notify's `rt` names a different resource type than the
        one already cached for `href`, so it was filed under the wrong href.

        Observe tokens are one byte and appliances appear to keep observers
        across sessions, so a stale relation can likely share a token with a
        new one -- the best explanation for a CAC cassette logging its
        /wind/direction/vs/0 modes against /mode/vs/0 (issue #509). Such a
        notify says nothing about `href`, so it is dropped before it counts
        as that href's push. Either side lacking `rt` is no evidence.
        """
        rt = rep.get("rt")
        cached_rt = (self.cache.get(href) or {}).get("rt")
        if not isinstance(rt, list) or not isinstance(cached_rt, list):
            return False
        if set(rt) == set(cached_rt):
            return False
        self.log.debug(
            "observe %s: dropping notify with rt %s, cached rt is %s", href, rt, cached_rt
        )
        return True

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
            self._last_notify_ts is not None and time.monotonic() - self._last_notify_ts < window_s
        )

    def subscribe_hrefs(self, session, hrefs: list[str]) -> set[str]:
        """Register OBSERVE on every href; returns the ones that took.
        Blocking — run in an executor.

        Split from the grace wait below (issue #294) so the coordinator can
        hold its session lock for just these sends -- each is fire-and-forget
        (DtlsCoapSession.subscribe doesn't wait for the device's ack), but
        since smartthings-local 0.1.9 it goes through the session's rate
        limiter, so the hold is now about one limiter interval per href
        (~200ms at the 5 req/s default). Still bounded, unlike the wait,
        which can block for the whole grace period and must not hold a lock
        a command write is also waiting on.
        """
        with self._notify_cond:
            self._notified.clear()
        subscribed: set[str] = set()
        for href in hrefs:
            segs = [s for s in href.strip("/").split("/") if s]
            try:
                session.subscribe(segs)
                subscribed.add(href)
            except Exception as e:
                self.log.warning("subscribe %s failed: %s", href, e)
        return subscribed

    def await_observe_notifies(
        self,
        subscribed: set[str],
        grace_period_s: float = GRACE_PERIOD_S,
        success_fraction: float = SUCCESS_FRACTION,
    ) -> bool:
        """Blocking — waits up to `grace_period_s`, returning early once
        `success_fraction` of `subscribed` have notified. Touches no
        session; safe to run without holding a session lock."""
        if not subscribed:
            return False

        def _fraction_reached() -> bool:
            return len(set(self._notified) & subscribed) / len(subscribed) >= success_fraction

        with self._notify_cond:
            return self._notify_cond.wait_for(_fraction_reached, timeout=grace_period_s)

    def enter_observe_mode(self, session, subscribed: set[str]) -> None:
        """Commit a successful attempt. Caller must have re-confirmed
        `session` is still the live one under its session lock (issue
        #294) -- committing against a session a reconnect already replaced
        would claim observe mode with nothing left to notice it's dead."""
        self.subscribed_hrefs = set(subscribed)
        # Issue #92: subscribed-but-silent hrefs are counted as covered by
        # push if we drop this, but they never emit a notify. Keep them on
        # the poll cadence via fallback_hrefs (otherwise idle in observe).
        # Same lock as on_notification's discard so a notify in this window
        # cannot land on a set object that is about to be replaced.
        with self._notify_cond:
            self.fallback_hrefs = set(subscribed) - self._notified
        self._set_mode(MODE_OBSERVE)
        self.start_refresh_task(session)

    def abandon_observe_attempt(self) -> None:
        """Drop a failed or stale attempt: no subscriptions worth keeping."""
        self._stop_refresh_task()
        self.subscribed_hrefs = set()
        self._set_mode(MODE_POLL)

    def try_enter_observe_mode(
        self,
        session,
        hrefs: list[str],
        grace_period_s: float = GRACE_PERIOD_S,
        success_fraction: float = SUCCESS_FRACTION,
    ) -> bool:
        """Blocking — subscribes to every href then waits up to
        `grace_period_s`, returning early once `success_fraction` of hrefs
        have notified. Caller must run this in an executor, never on the
        event loop.

        Single-threaded convenience wrapper around the phase split above
        (subscribe_hrefs / await_observe_notifies / enter_observe_mode /
        abandon_observe_attempt) for callers -- direct and most existing
        tests -- that don't need the lock-scoping those phases exist for."""
        subscribed = self.subscribe_hrefs(session, hrefs)
        if not subscribed:
            self.abandon_observe_attempt()
            return False
        if self.await_observe_notifies(subscribed, grace_period_s, success_fraction):
            self.enter_observe_mode(session, subscribed)
            return True
        self.abandon_observe_attempt()
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
                    href,
                    diff,
                )
        return found

    def downgrade_to_poll(self) -> None:
        self.fallback_hrefs = set(self.subscribed_hrefs)
        self.subscribed_hrefs = set()
        self._stop_refresh_task()
        self._set_mode(MODE_POLL)

    def start_refresh_task(self, session) -> None:
        self._stop_refresh_task()
        paths = [tuple(h.strip("/").split("/")) for h in self.subscribed_hrefs]
        self._refresh_task = ObserveRefreshTask(
            session,
            paths,
            interval_s=REFRESH_INTERVAL_S,
            logger=self.log,
        )
        self._refresh_stop = threading.Event()
        self._refresh_thread = threading.Thread(
            target=self._refresh_task.run_forever,
            args=(self._refresh_stop,),
            daemon=True,
            name="localthings-observe-refresh",
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
