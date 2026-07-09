"""Tests for ObserveManager: write-settle guard and mode defaults."""
from __future__ import annotations

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

    def _notify_all():
        for href in hrefs:
            mgr.on_notification(href, cbor2.dumps({'x': 1}))

    # Grace period is real time.sleep() in the manager; keep it tiny and
    # notify synchronously beforehand so notifies are already recorded
    # when the (short) sleep completes.
    _notify_all()
    entered = mgr.try_enter_observe_mode(session, hrefs, grace_period_s=0.01)

    assert entered is True
    assert mgr.mode == 'observe'
    assert mgr.subscribed_hrefs == set(hrefs)


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
    for href in hrefs[:3]:  # 3/4 = 0.75, below default 0.8 threshold
        mgr.on_notification(href, cbor2.dumps({'x': 1}))

    entered = mgr.try_enter_observe_mode(
        session, hrefs, grace_period_s=0.01, success_fraction=0.7,
    )

    assert entered is True
    assert mgr.mode == 'observe'


def test_on_notification_ignores_malformed_cbor():
    mgr = _manager()
    mgr.on_notification('/oven/vs/0', b'\xff\xff\xff not cbor')
    assert mgr.cache.get('/oven/vs/0') is None
