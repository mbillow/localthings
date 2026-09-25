"""Tests for ObserveManager: write-settle guard and mode defaults."""

from __future__ import annotations

import threading
import time

import cbor2
from smartthings_local.ocf.state_cache import StateCache

from custom_components.localthings.observe import (
    MODE_POLL,
    STALE_SWEEPS_TO_FALLBACK,
    ObserveManager,
    _rep_diff,
)


class _NullDescriptor:
    def on_observation(self, state, href, rep):
        return None


def _manager() -> ObserveManager:
    return ObserveManager(StateCache(_NullDescriptor()))


def test_starts_in_poll_mode():
    mgr = _manager()
    assert mgr.mode == MODE_POLL


def test_apply_writes_through_when_not_settling():
    mgr = _manager()
    assert mgr.apply("/oven/vs/0", {"a": 1}, source="poll") is True
    assert mgr.cache.get("/oven/vs/0") == {"a": 1}


def test_apply_merges_partial_update_onto_prior_rep():
    """Regression test for issue #27: a Bespoke fridge's /mode/vs/0 notify
    (and even a later sweep entry for that href) can carry only `modes`,
    omitting `supportedOptions` entirely. A full replace would wipe
    `supportedOptions` from the cache the moment that partial update
    arrives, even though the device's supported options didn't change --
    which is exactly what made the flex-zone select disappear."""
    mgr = _manager()
    full = {
        "x.com.samsung.da.modes": ["CVN_CONVERTIBLE_ZONE", "CV_FDR_MEAT"],
        "x.com.samsung.da.supportedOptions": ["CV_FDR_WINE", "CV_FDR_MEAT"],
    }
    mgr.apply("/mode/vs/0", full, source="poll")

    partial = {"x.com.samsung.da.modes": ["CVN_CONVERTIBLE_ZONE", "WATERFILTER_ENABLE"]}
    mgr.apply("/mode/vs/0", partial, source="observe")

    cached = mgr.cache.get("/mode/vs/0")
    assert cached is not None
    assert cached["x.com.samsung.da.modes"] == ["CVN_CONVERTIBLE_ZONE", "WATERFILTER_ENABLE"]
    assert cached["x.com.samsung.da.supportedOptions"] == ["CV_FDR_WINE", "CV_FDR_MEAT"]


def test_apply_fully_replaces_alarms_href_instead_of_merging():
    """Regression test for issue #348: /alarms/vs/0's `items` array is a
    complete snapshot of every currently-active alarm, not a partial field
    update like /mode/vs/0 (issue #27). A washer's board reports a cleared
    alarm by omitting `items` entirely -- a live read_resource GET showed
    `{}` -- so merging that onto the prior rep (as every other href does)
    left the stale ErrorCode_DC entry in the cache forever. This must
    instead behave like a full replace: the empty rep wins outright."""
    mgr = _manager()
    active = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.code": "ErrorCode_DC", "x.com.samsung.da.state": "Created"}
        ]
    }
    mgr.apply("/alarms/vs/0", active, source="poll")
    assert mgr.cache.get("/alarms/vs/0") == active

    cleared = mgr.apply("/alarms/vs/0", {}, source="poll")

    assert cleared is True
    assert mgr.cache.get("/alarms/vs/0") == {}


def test_apply_fully_replaces_alarms_href_for_subdevice_shapes():
    """The same full-replace behavior must hold for both hrefs
    `Subdevice.to_actual` can produce: an indexed subdevice renumbers only
    the trailing '0' (/alarms/vs/1), and a prefixed one prepends a UUID
    (/<uuid>/alarms/vs/0) -- neither ever touches the 'alarms/vs' stem
    itself (registry/subdevices.py)."""
    for href in ("/alarms/vs/1", "/6c2dff6d-ee5c-dad1-6a5e-000000000001/alarms/vs/0"):
        mgr = _manager()
        mgr.apply(
            href,
            {"x.com.samsung.da.items": [{"x.com.samsung.da.code": "ErrorCode_UB"}]},
            source="poll",
        )

        cleared = mgr.apply(href, {}, source="poll")

        assert cleared is True
        assert mgr.cache.get(href) == {}


def test_apply_drops_update_during_settle_window():
    mgr = _manager()
    mgr.cache.apply_rep("/oven/vs/0", {"a": 1}, source="seed")
    mgr.mark_write_pending("/oven/vs/0", settle_s=1.0)

    result = mgr.apply("/oven/vs/0", {"a": 2}, source="poll")

    assert result is False
    assert mgr.cache.get("/oven/vs/0") == {"a": 1}


def test_apply_optimistic_bypasses_an_in_progress_settle_window():
    """Regression for issue #9: /course/vs/0 backs several independent
    washer selects (cycle, detergent quantity, softener quantity, ...).
    Picking a second one while the first's settle window is still open
    (now sized to tens of seconds -- see coordinator._POST_TIMEOUT_S/
    _POLL_TIMEOUT_S) is a normal sequence, not a stale echo of the first
    write, and must land in the cache immediately -- not get silently
    dropped by a guard that exists to protect optimistic writes, not
    suppress them."""
    mgr = _manager()
    mgr.cache.apply_rep("/course/vs/0", {"Course": "1C", "Detergent": "1"}, source="seed")
    mgr.mark_write_pending("/course/vs/0", settle_s=30.0)

    result = mgr.apply("/course/vs/0", {"Detergent": "2"}, source="optimistic")

    assert result is True
    assert mgr.cache.get("/course/vs/0") == {"Course": "1C", "Detergent": "2"}

    # A poll/sweep/observe update racing in right behind it is still
    # gated -- the second write's own guard (re-armed by mark_write_pending,
    # not exercised directly here) is what protects it going forward.
    assert mgr.apply("/course/vs/0", {"Detergent": "1"}, source="poll") is False


def test_apply_accepts_update_after_settle_window_elapses():
    mgr = _manager()
    mgr.mark_write_pending("/oven/vs/0", settle_s=0.05)
    time.sleep(0.1)

    result = mgr.apply("/oven/vs/0", {"a": 2}, source="poll")

    assert result is True
    assert mgr.cache.get("/oven/vs/0") == {"a": 2}


class _FakeSession:
    """Minimal stand-in for DtlsCoapSession.subscribe(), for observe tests.

    `notify_after_subscribe` maps href -> cbor-encodable rep. Call
    `subscribe()` records the href; the test then calls
    `mgr.on_notification(href, payload)` itself to simulate delivery,
    since real notify delivery is async/threaded in production.
    """

    def __init__(self):
        self.subscribed: list[str] = []
        self.fail_hrefs: set[str] = set()

    def subscribe(self, path_segs):
        href = "/" + "/".join(path_segs)
        if href in self.fail_hrefs:
            raise ConnectionError("subscribe failed")
        self.subscribed.append(href)
        return b"\x01"


def test_try_enter_observe_mode_succeeds_when_all_hrefs_notify():
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/oven/vs/0", "/power/vs/0"]

    def _notify_during_grace_period():
        # Simulate notifications arriving during the grace period
        time.sleep(0.005)  # Let the sleep start, then notify partway through
        for href in hrefs:
            mgr.on_notification(href, cbor2.dumps({"x": 1}))

    # Start background thread to deliver notifications during grace period
    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    try:
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier.join()

        assert entered is True
        assert mgr.mode == "observe"
        assert mgr.subscribed_hrefs == set(hrefs)
        assert mgr.fallback_hrefs == set()
    finally:
        mgr.close()


def test_try_enter_observe_mode_falls_back_when_no_notifies_arrive():
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/oven/vs/0", "/power/vs/0"]

    entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.01)

    assert entered is False
    assert mgr.mode == "poll"
    assert mgr.subscribed_hrefs == set()


def test_try_enter_observe_mode_falls_back_when_subscribe_fails_for_all():
    mgr = _manager()
    session = _FakeSession()
    session.fail_hrefs = {"/oven/vs/0", "/power/vs/0"}
    hrefs = ["/oven/vs/0", "/power/vs/0"]

    entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.01)

    assert entered is False
    assert mgr.mode == "poll"


def test_enter_observe_mode_keeps_silent_hrefs_on_fallback():
    """Issue #92: subscribed hrefs that never notified stay on the poll
    cadence via fallback_hrefs, rather than being treated as push-covered."""
    mgr = _manager()
    session = _FakeSession()
    subscribed = {"/a/vs/0", "/b/vs/0", "/c/vs/0"}
    mgr.on_notification("/a/vs/0", cbor2.dumps({"x": 1}))
    mgr.on_notification("/b/vs/0", cbor2.dumps({"x": 1}))
    try:
        mgr.enter_observe_mode(session, subscribed)
        assert mgr.mode == "observe"
        assert mgr.subscribed_hrefs == subscribed
        assert mgr.fallback_hrefs == {"/c/vs/0"}
    finally:
        mgr.close()


def test_on_notification_drops_href_from_fallback():
    """A late first notify (after the 80% snapshot) self-corrects
    fallback_hrefs so a slow-but-pushing href is not polled for the rest
    of the session -- multi-block resources being the reliable victim."""
    mgr = _manager()
    session = _FakeSession()
    subscribed = {"/a/vs/0", "/b/vs/0", "/c/vs/0"}
    mgr.on_notification("/a/vs/0", cbor2.dumps({"x": 1}))
    mgr.on_notification("/b/vs/0", cbor2.dumps({"x": 1}))
    try:
        mgr.enter_observe_mode(session, subscribed)
        assert mgr.fallback_hrefs == {"/c/vs/0"}
        mgr.on_notification("/c/vs/0", cbor2.dumps({"x": 1}))
        assert mgr.fallback_hrefs == set()
    finally:
        mgr.close()


def test_try_enter_observe_mode_meets_success_fraction_with_partial_notifies():
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/a/vs/0", "/b/vs/0", "/c/vs/0", "/d/vs/0"]

    def _notify_partial():
        # Simulate partial notifications arriving during grace period
        time.sleep(0.005)
        for href in hrefs[:3]:  # 3/4 = 0.75
            mgr.on_notification(href, cbor2.dumps({"x": 1}))

    notifier = threading.Thread(target=_notify_partial, daemon=True)
    notifier.start()

    try:
        entered = mgr.try_enter_observe_mode(
            session,
            hrefs,
            grace_period_s=0.02,
            success_fraction=0.7,
        )
        notifier.join()

        assert entered is True
        assert mgr.mode == "observe"
        assert mgr.fallback_hrefs == {hrefs[3]}
    finally:
        mgr.close()


def test_on_notification_ignores_malformed_cbor():
    mgr = _manager()
    mgr.on_notification("/oven/vs/0", b"\xff\xff\xff not cbor")
    assert mgr.cache.get("/oven/vs/0") is None


def test_try_enter_observe_mode_clears_stale_notifications_on_retry():
    """Regression test: verify second call to try_enter_observe_mode() doesn't
    get polluted by notifications from the first call. This happens in real
    usage when poll-only mode periodically retries entering observe mode."""
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/a/vs/0", "/b/vs/0"]

    try:
        # First call: all hrefs notify -> succeeds and enters observe mode
        def _notify_all_first():
            time.sleep(0.005)
            for href in hrefs:
                mgr.on_notification(href, cbor2.dumps({"x": 1}))

        notifier1 = threading.Thread(target=_notify_all_first, daemon=True)
        notifier1.start()
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier1.join()
        assert entered is True
        assert mgr.mode == "observe"

        # Second call: retry with same hrefs, but only one notifies.
        # Without the fix (missing self._notified.clear()), the old notifications
        # would leak in, making this appear successful (2/2 instead of 1/2).
        # With the fix, it should fail because only 1/2 < 0.8.
        session.subscribed = []  # reset for clean test

        def _notify_partial_second():
            time.sleep(0.005)
            mgr.on_notification(hrefs[0], cbor2.dumps({"x": 2}))  # only one notifies

        notifier2 = threading.Thread(target=_notify_partial_second, daemon=True)
        notifier2.start()
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier2.join()

        assert entered is False
        assert mgr.mode == "poll"
        assert mgr.subscribed_hrefs == set()
    finally:
        mgr.close()


def test_log_sweep_discrepancies_noop_when_not_in_observe_mode(caplog):
    mgr = _manager()
    with caplog.at_level("DEBUG"):
        mgr.log_sweep_discrepancies({"/oven/vs/0": {"a": 2}})
    assert "observe missed a change" not in caplog.text


def test_log_sweep_discrepancies_never_changes_mode():
    """A sweep/cache mismatch is diagnostic-only — a still-live OBSERVE
    session is never torn down over a data-drift inference. The 30s sweep
    already re-applies the authoritative state to the cache regardless of
    mode, so there's nothing a downgrade would fix; it would only throw
    away working push coverage on every other subscribed href."""
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/oven/vs/0", "/power/vs/0"]

    def _notify_during_grace_period():
        time.sleep(0.005)
        for href in hrefs:
            mgr.on_notification(href, cbor2.dumps({"a": 1}))

    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    try:
        mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier.join()
        assert mgr.mode == "observe"

        # Sweep sees values the cache never got via notify on BOTH hrefs.
        mgr.log_sweep_discrepancies({hrefs[0]: {"a": 2}, hrefs[1]: {"a": 2}})

        assert mgr.mode == "observe"
    finally:
        mgr.close()


def _observing(mgr: ObserveManager, hrefs: list[str]) -> None:
    """Enter observe mode with every href having pushed once."""
    for href in hrefs:
        mgr.on_notification(href, cbor2.dumps({"t": 0}))
    mgr.enter_observe_mode(_FakeSession(), set(hrefs))
    assert mgr.fallback_hrefs == set()


def _sweep(mgr: ObserveManager, resources: dict[str, dict]) -> None:
    """What the coordinator does with each sweep: compare, then apply."""
    mgr.log_sweep_discrepancies(resources)
    for href, rep in resources.items():
        mgr.apply(href, rep, source="sweep")


def test_href_the_sweep_keeps_finding_stale_goes_back_on_fallback():
    """Issue #507: a range's /temperatures/vs/0 pushes once, then climbs
    through a preheat without another notify. Once the sweep has found it
    stale enough times in a row, it is sub-polled again like an href that
    never pushed (issue #92)."""
    mgr = _manager()
    href = "/temperatures/vs/0"
    _observing(mgr, [href, "/power/vs/0"])
    try:
        for t in range(1, STALE_SWEEPS_TO_FALLBACK):
            _sweep(mgr, {href: {"t": t}, "/power/vs/0": {"t": 0}})
            assert mgr.fallback_hrefs == set()
        _sweep(mgr, {href: {"t": STALE_SWEEPS_TO_FALLBACK}, "/power/vs/0": {"t": 0}})
        assert mgr.fallback_hrefs == {href}
        assert mgr.mode == "observe"
    finally:
        mgr.close()


def test_a_fresh_sweep_resets_the_stale_count():
    """Only consecutive stale sweeps count, so an href that misses one
    change now and then stays push-covered."""
    mgr = _manager()
    href = "/temperatures/vs/0"
    _observing(mgr, [href])
    try:
        for t in range(1, STALE_SWEEPS_TO_FALLBACK * 2):
            _sweep(mgr, {href: {"t": t}})
            _sweep(mgr, {href: {"t": t}})
        assert mgr.fallback_hrefs == set()
    finally:
        mgr.close()


def test_a_notify_takes_a_stale_href_back_off_fallback():
    mgr = _manager()
    href = "/temperatures/vs/0"
    _observing(mgr, [href])
    try:
        for t in range(1, STALE_SWEEPS_TO_FALLBACK + 1):
            _sweep(mgr, {href: {"t": t}})
        assert mgr.fallback_hrefs == {href}

        mgr.on_notification(href, cbor2.dumps({"t": 99}))
        assert mgr.fallback_hrefs == set()
        # The count restarted with the notify, so one stale sweep is not
        # enough to demote it again.
        _sweep(mgr, {href: {"t": 100}})
        assert mgr.fallback_hrefs == set()
    finally:
        mgr.close()


def test_rep_diff_reports_only_shared_fields_that_differ():
    """Fields present on only one side (a shape difference, e.g. batch
    sweep vs. individual GET/notify returning different fields for the
    same href) must not be reported — only shared fields with different
    values."""
    diff = _rep_diff({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 5, "d": 9})

    assert diff == {"b": (2, 5)}


def test_log_sweep_discrepancies_ignores_shape_only_differences(caplog):
    """A sweep rep missing/adding fields the cache doesn't have (or vice
    versa) is a representation-shape difference, not a missed change —
    must not be logged as a discrepancy."""
    mgr = _manager()
    session = _FakeSession()
    href = "/oven/vs/0"

    def _notify_during_grace_period():
        time.sleep(0.005)
        mgr.on_notification(href, cbor2.dumps({"a": 1, "rt": ["x"], "if": ["y"]}))

    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    try:
        mgr.try_enter_observe_mode(session, [href], grace_period_s=0.02)
        notifier.join()
        assert mgr.mode == "observe"

        # Sweep rep has the same 'a' value but lacks rt/if and adds 'href' —
        # a shape difference (batch interface), not a missed content change.
        with caplog.at_level("DEBUG"):
            mgr.log_sweep_discrepancies({href: {"a": 1, "href": href}})
    finally:
        mgr.close()

    assert "observe missed a change" not in caplog.text


def test_log_sweep_discrepancies_includes_the_diff(caplog):
    """The log line must include what actually differed, not just that it
    did."""
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/oven/vs/0", "/power/vs/0"]

    def _notify_during_grace_period():
        time.sleep(0.005)
        for href in hrefs:
            mgr.on_notification(href, cbor2.dumps({"a": 1, "b": 2}))

    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    try:
        mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier.join()
        assert mgr.mode == "observe"

        with caplog.at_level("DEBUG"):
            mgr.log_sweep_discrepancies({hrefs[0]: {"a": 1, "b": 5}, hrefs[1]: {"a": 1, "b": 9}})

        assert "{'b': (2, 5)}" in caplog.text
        assert mgr.mode == "observe"
    finally:
        mgr.close()


def test_log_sweep_discrepancies_ignores_href_during_settle_window(caplog):
    mgr = _manager()
    session = _FakeSession()
    href = "/oven/vs/0"
    mgr.on_notification(href, cbor2.dumps({"a": 1}))
    mgr.try_enter_observe_mode(session, [href], grace_period_s=0.01)
    mgr.mark_write_pending(href, settle_s=5.0)

    with caplog.at_level("DEBUG"):
        mgr.log_sweep_discrepancies({href: {"a": 2}})

    assert "observe missed a change" not in caplog.text


def test_downgrade_to_poll_moves_subscribed_to_fallback():
    mgr = _manager()
    session = _FakeSession()
    href = "/oven/vs/0"

    def _notify_during_grace_period():
        time.sleep(0.005)
        mgr.on_notification(href, cbor2.dumps({"a": 1}))

    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    mgr.try_enter_observe_mode(session, [href], grace_period_s=0.02)
    notifier.join()

    mgr.downgrade_to_poll()

    assert mgr.mode == "poll"
    assert mgr.subscribed_hrefs == set()
    assert mgr.fallback_hrefs == {href}


def test_start_refresh_task_spawns_a_daemon_thread():
    mgr = _manager()
    mgr.subscribed_hrefs = {"/oven/vs/0"}
    session = _FakeSession()

    mgr.start_refresh_task(session)
    try:
        assert mgr._refresh_thread is not None
        assert mgr._refresh_thread.is_alive()
        assert mgr._refresh_thread.daemon is True
    finally:
        mgr.close()


def test_close_stops_the_refresh_thread():
    mgr = _manager()
    mgr.subscribed_hrefs = {"/oven/vs/0"}
    session = _FakeSession()
    mgr.start_refresh_task(session)
    thread = mgr._refresh_thread
    assert thread is not None

    mgr.close()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert mgr._refresh_thread is None


def test_downgrade_to_poll_stops_refresh_task():
    mgr = _manager()
    mgr.subscribed_hrefs = {"/oven/vs/0"}
    session = _FakeSession()
    mgr.start_refresh_task(session)
    thread = mgr._refresh_thread
    assert thread is not None

    mgr.downgrade_to_poll()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert mgr._refresh_thread is None


def test_try_enter_observe_mode_exits_early_when_fraction_reached():
    """The grace wait returns as soon as enough hrefs notify, not after the
    whole grace ceiling. Production data (fridge TP2X_REF_20K, 2026-07-30):
    all subscribed hrefs notify within ~0.34s of subscribe, yet the old fixed
    time.sleep(15) waited the remaining ~14.6s anyway — every successful
    first-refresh / observe-retry fetch paid that dead time."""
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/a/vs/0", "/b/vs/0"]

    def _notify_after_subscribe():
        # Let try_enter clear _notified + subscribe first (matches the existing
        # test pattern's 0.005 lead), then notify well before the 2s ceiling.
        time.sleep(0.01)
        for href in hrefs:
            mgr.on_notification(href, cbor2.dumps({"x": 1}))

    notifier = threading.Thread(target=_notify_after_subscribe, daemon=True)
    notifier.start()

    try:
        t0 = time.monotonic()
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=2.0)
        elapsed = time.monotonic() - t0
        notifier.join()

        assert entered is True
        assert mgr.mode == "observe"
        # Early exit: returns near the notify (~0.01s), not the 2.0s ceiling.
        # The old fixed sleep would make this >= 2.0.
        assert elapsed < 0.5
    finally:
        mgr.close()


def test_try_enter_observe_mode_waits_full_ceiling_when_fraction_not_reached():
    """Early-exit must NOT fire below the success fraction. A partial set of
    notifies that doesn't meet the threshold waits the full grace ceiling,
    then falls back to poll — identical to the old fixed sleep. Guards
    against an over-eager predicate that enters observe on too few notifies."""
    mgr = _manager()
    session = _FakeSession()
    hrefs = ["/a/vs/0", "/b/vs/0", "/c/vs/0", "/d/vs/0"]  # 4 hrefs

    def _notify_one():
        time.sleep(0.01)
        mgr.on_notification(hrefs[0], cbor2.dumps({"x": 1}))  # 1/4 = 0.25

    notifier = threading.Thread(target=_notify_one, daemon=True)
    notifier.start()

    try:
        t0 = time.monotonic()
        entered = mgr.try_enter_observe_mode(
            session,
            hrefs,
            grace_period_s=0.2,
            success_fraction=0.8,
        )
        elapsed = time.monotonic() - t0
        notifier.join()

        assert entered is False
        assert mgr.mode == "poll"
        assert mgr.subscribed_hrefs == set()
        # Waited the full 0.2s ceiling (1/4 = 0.25 < 0.8); did not early-exit.
        assert elapsed >= 0.18
    finally:
        mgr.close()


def test_on_notification_drops_rep_whose_rt_names_another_resource():
    """Issue #509: a /wind/direction/vs/0 rep filed under /mode/vs/0 (a
    shared Observe token) must not overwrite the mode rep, nor count as
    /mode/vs/0 having pushed."""
    mgr = _manager()
    mode = {
        "x.com.samsung.da.modes": ["Cool"],
        "x.com.samsung.da.supportedModes": ["Auto", "Cool", "Dry", "Fan", "Heat"],
        "rt": ["x.com.samsung.da.mode"],
    }
    mgr.apply("/mode/vs/0", mode, source="poll")
    mgr.fallback_hrefs = {"/mode/vs/0"}

    wind = {
        "x.com.samsung.da.modes": "Fix",
        "x.com.samsung.da.supportedModes": ["Up_And_Low", "Fix", "Individual"],
        "rt": ["x.com.samsung.da.wind.direction"],
    }
    mgr.on_notification("/mode/vs/0", cbor2.dumps(wind))

    assert mgr.cache.get("/mode/vs/0") == mode
    assert mgr.fallback_hrefs == {"/mode/vs/0"}
    assert "/mode/vs/0" not in mgr._notified


def test_on_notification_applies_rep_when_rt_matches_or_is_absent():
    mgr = _manager()
    mgr.apply(
        "/mode/vs/0",
        {"x.com.samsung.da.modes": ["Cool"], "rt": ["x.com.samsung.da.mode"]},
        source="poll",
    )

    mgr.on_notification(
        "/mode/vs/0",
        cbor2.dumps({"x.com.samsung.da.modes": ["Heat"], "rt": ["x.com.samsung.da.mode"]}),
    )
    assert (mgr.cache.get("/mode/vs/0") or {}).get("x.com.samsung.da.modes") == ["Heat"]

    mgr.on_notification("/mode/vs/0", cbor2.dumps({"x.com.samsung.da.modes": ["Dry"]}))
    assert (mgr.cache.get("/mode/vs/0") or {}).get("x.com.samsung.da.modes") == ["Dry"]
