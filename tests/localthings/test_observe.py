"""Tests for ObserveManager: write-settle guard and mode defaults."""
from __future__ import annotations

import time

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
