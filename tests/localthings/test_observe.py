"""Tests for ObserveManager: write-settle guard and mode defaults."""
from __future__ import annotations

import threading
import time

import cbor2
import pytest

from smartthings_local.ocf.state_cache import StateCache

from custom_components.localthings.observe import ObserveManager, MODE_POLL


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
    assert mgr.apply('/oven/vs/0', {'a': 1}, source='poll') is True
    assert mgr.cache.get('/oven/vs/0') == {'a': 1}


def test_apply_drops_update_during_settle_window():
    mgr = _manager()
    mgr.cache.apply_rep('/oven/vs/0', {'a': 1}, source='seed')
    mgr.mark_write_pending('/oven/vs/0', settle_s=1.0)

    result = mgr.apply('/oven/vs/0', {'a': 2}, source='poll')

    assert result is False
    assert mgr.cache.get('/oven/vs/0') == {'a': 1}


def test_apply_accepts_update_after_settle_window_elapses():
    mgr = _manager()
    mgr.mark_write_pending('/oven/vs/0', settle_s=0.05)
    time.sleep(0.1)

    result = mgr.apply('/oven/vs/0', {'a': 2}, source='poll')

    assert result is True
    assert mgr.cache.get('/oven/vs/0') == {'a': 2}


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
        href = '/' + '/'.join(path_segs)
        if href in self.fail_hrefs:
            raise ConnectionError("subscribe failed")
        self.subscribed.append(href)
        return b'\x01'


def test_try_enter_observe_mode_succeeds_when_all_hrefs_notify():
    mgr = _manager()
    session = _FakeSession()
    hrefs = ['/oven/vs/0', '/power/vs/0']

    def _notify_during_grace_period():
        # Simulate notifications arriving during the grace period
        time.sleep(0.005)  # Let the sleep start, then notify partway through
        for href in hrefs:
            mgr.on_notification(href, cbor2.dumps({'x': 1}))

    # Start background thread to deliver notifications during grace period
    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    try:
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier.join()

        assert entered is True
        assert mgr.mode == 'observe'
        assert mgr.subscribed_hrefs == set(hrefs)
    finally:
        mgr.close()


def test_try_enter_observe_mode_falls_back_when_no_notifies_arrive():
    mgr = _manager()
    session = _FakeSession()
    hrefs = ['/oven/vs/0', '/power/vs/0']

    entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.01)

    assert entered is False
    assert mgr.mode == 'poll'
    assert mgr.subscribed_hrefs == set()


def test_try_enter_observe_mode_falls_back_when_subscribe_fails_for_all():
    mgr = _manager()
    session = _FakeSession()
    session.fail_hrefs = {'/oven/vs/0', '/power/vs/0'}
    hrefs = ['/oven/vs/0', '/power/vs/0']

    entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.01)

    assert entered is False
    assert mgr.mode == 'poll'


def test_try_enter_observe_mode_meets_success_fraction_with_partial_notifies():
    mgr = _manager()
    session = _FakeSession()
    hrefs = ['/a/vs/0', '/b/vs/0', '/c/vs/0', '/d/vs/0']

    def _notify_partial():
        # Simulate partial notifications arriving during grace period
        time.sleep(0.005)
        for href in hrefs[:3]:  # 3/4 = 0.75
            mgr.on_notification(href, cbor2.dumps({'x': 1}))

    notifier = threading.Thread(target=_notify_partial, daemon=True)
    notifier.start()

    try:
        entered = mgr.try_enter_observe_mode(
            session, hrefs, grace_period_s=0.02, success_fraction=0.7,
        )
        notifier.join()

        assert entered is True
        assert mgr.mode == 'observe'
    finally:
        mgr.close()


def test_on_notification_ignores_malformed_cbor():
    mgr = _manager()
    mgr.on_notification('/oven/vs/0', b'\xff\xff\xff not cbor')
    assert mgr.cache.get('/oven/vs/0') is None


def test_try_enter_observe_mode_clears_stale_notifications_on_retry():
    """Regression test: verify second call to try_enter_observe_mode() doesn't
    get polluted by notifications from the first call. This happens in real
    usage when poll-only mode periodically retries entering observe mode."""
    mgr = _manager()
    session = _FakeSession()
    hrefs = ['/a/vs/0', '/b/vs/0']

    try:
        # First call: all hrefs notify -> succeeds and enters observe mode
        def _notify_all_first():
            time.sleep(0.005)
            for href in hrefs:
                mgr.on_notification(href, cbor2.dumps({'x': 1}))

        notifier1 = threading.Thread(target=_notify_all_first, daemon=True)
        notifier1.start()
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier1.join()
        assert entered is True
        assert mgr.mode == 'observe'

        # Second call: retry with same hrefs, but only one notifies.
        # Without the fix (missing self._notified.clear()), the old notifications
        # would leak in, making this appear successful (2/2 instead of 1/2).
        # With the fix, it should fail because only 1/2 < 0.8.
        session.subscribed = []  # reset for clean test

        def _notify_partial_second():
            time.sleep(0.005)
            mgr.on_notification(hrefs[0], cbor2.dumps({'x': 2}))  # only one notifies

        notifier2 = threading.Thread(target=_notify_partial_second, daemon=True)
        notifier2.start()
        entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.02)
        notifier2.join()

        assert entered is False
        assert mgr.mode == 'poll'
        assert mgr.subscribed_hrefs == set()
    finally:
        mgr.close()


def test_check_sweep_for_misses_false_when_not_in_observe_mode():
    mgr = _manager()
    assert mgr.check_sweep_for_misses({'/oven/vs/0': {'a': 2}}) is False


def test_check_sweep_for_misses_detects_mismatch():
    mgr = _manager()
    session = _FakeSession()
    href = '/oven/vs/0'

    def _notify_during_grace_period():
        time.sleep(0.005)
        mgr.on_notification(href, cbor2.dumps({'a': 1}))

    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    try:
        mgr.try_enter_observe_mode(session, [href], grace_period_s=0.02)
        notifier.join()
        assert mgr.mode == 'observe'

        # Sweep sees a value the cache never got via notify -> observe missed it.
        missed = mgr.check_sweep_for_misses({href: {'a': 2}})

        assert missed is True
    finally:
        mgr.close()


def test_check_sweep_for_misses_ignores_href_during_settle_window():
    mgr = _manager()
    session = _FakeSession()
    href = '/oven/vs/0'
    mgr.on_notification(href, cbor2.dumps({'a': 1}))
    mgr.try_enter_observe_mode(session, [href], grace_period_s=0.01)
    mgr.mark_write_pending(href, settle_s=5.0)

    missed = mgr.check_sweep_for_misses({href: {'a': 2}})

    assert missed is False


def test_downgrade_to_poll_moves_subscribed_to_fallback():
    mgr = _manager()
    session = _FakeSession()
    href = '/oven/vs/0'

    def _notify_during_grace_period():
        time.sleep(0.005)
        mgr.on_notification(href, cbor2.dumps({'a': 1}))

    notifier = threading.Thread(target=_notify_during_grace_period, daemon=True)
    notifier.start()

    mgr.try_enter_observe_mode(session, [href], grace_period_s=0.02)
    notifier.join()

    mgr.downgrade_to_poll()

    assert mgr.mode == 'poll'
    assert mgr.subscribed_hrefs == set()
    assert mgr.fallback_hrefs == {href}


def test_start_refresh_task_spawns_a_daemon_thread():
    mgr = _manager()
    mgr.subscribed_hrefs = {'/oven/vs/0'}
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
    mgr.subscribed_hrefs = {'/oven/vs/0'}
    session = _FakeSession()
    mgr.start_refresh_task(session)
    thread = mgr._refresh_thread

    mgr.close()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert mgr._refresh_thread is None


def test_downgrade_to_poll_stops_refresh_task():
    mgr = _manager()
    mgr.subscribed_hrefs = {'/oven/vs/0'}
    session = _FakeSession()
    mgr.start_refresh_task(session)
    thread = mgr._refresh_thread

    mgr.downgrade_to_poll()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert mgr._refresh_thread is None
