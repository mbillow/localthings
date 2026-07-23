"""Tests for the LocalThingsCoordinator."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import cbor2
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir

from custom_components.localthings.const import (
    CONF_HOST, DOMAIN, SUMMARY_INTERVAL_S,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities.common import (
    remote_control_enabled,
)
from custom_components.localthings.observe import MODE_OBSERVE, MODE_POLL, PUSH_HEALTH_WINDOW_S

from .conftest import ENTRY_DATA, MOCK_SERIAL


async def test_first_refresh_runs_discovery(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """After first refresh, coordinator.bound is populated."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.bound
    assert coordinator.data


async def test_device_info_populated(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """device_info is set from discovered resources after first refresh."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.device_info is not None
    assert any(
        DOMAIN in str(i)
        for i in coordinator.device_info.get('identifiers', set())
    )


async def test_summary_interval(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """Summary poll interval is always SUMMARY_INTERVAL_S."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.update_interval == timedelta(seconds=SUMMARY_INTERVAL_S)


async def test_update_failed_on_persistent_poll_error(
    hass: HomeAssistant, mock_entry
) -> None:
    """ConfigEntryNotReady raised when poll fails even after reconnect."""
    from homeassistant.exceptions import ConfigEntryNotReady

    with (
        patch('custom_components.localthings.coordinator.LocalThingsCoordinator._connect_session'),
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            side_effect=RuntimeError('connection lost'),
        ),
        patch('custom_components.localthings.coordinator.LocalThingsCoordinator._close_session'),
    ):
        result = await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    # Setup fails — entry should not be in hass.data
    assert not result or mock_entry.entry_id not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# Coverage-gap detection and the Repairs issue it raises
# ---------------------------------------------------------------------------

async def test_discovery_populates_device_type_with_no_unbound_hrefs(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """The real refrigerator fixture is a recognized type, fully covered.

    Every href in this fixture is either mapped to a capability or listed
    in capabilities.ignored — see tests/test_registry_ignored.py for the
    noise-suppression checks. If this starts failing because a genuinely
    new gap appeared, map it or ignore it rather than deleting the assertion.
    """
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.device_type_name == 'refrigerator'
    assert coordinator.one_ui_version == '7.0 Refrigerator'
    assert coordinator._unbound_hrefs == []


async def test_coverage_gap_issue_absent_for_fully_covered_real_fixture(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """A recognized device with no unbound hrefs does not raise the issue."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_run_discovery_detects_washer_via_model_fallback(
    hass: HomeAssistant, mock_entry
) -> None:
    """Washer hardware reports no oneUiVersion; device type must resolve
    via for_device_by_model instead of falling back to generic CAPABILITIES."""
    resources = {
        '/information/vs/0': {
            'x.com.samsung.da.modelNum':
                'DA_WM_TP1_21_COMMON|20375141|20010002001811424AA30217008A0000',
            'x.com.samsung.da.description':
                'DA_WM_TP1_21_COMMON_WW5000C/DC92-03495A_B048',
            'x.com.samsung.da.serialNum': 'TEST-SERIAL',
        },
        '/otninformation/vs/0': {'otnStatus': 'None'},
        '/power/vs/0': {'x.com.samsung.da.power': 'On'},
    }
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._run_discovery(resources)
    assert coordinator.device_type_name == 'washer'


def test_run_discovery_detects_cooktop_via_resource_signature(
    hass: HomeAssistant, mock_entry
) -> None:
    """NA9300K omits oneUiVersion and information but has burner options."""
    from tests.conftest import _load_device

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._run_discovery(_load_device('cooktop'))

    assert coordinator.device_type_name == 'cooktop'
    assert coordinator._unbound_hrefs == []


def test_run_discovery_detects_range_hood_via_model(
    hass: HomeAssistant, mock_entry
) -> None:
    from tests.conftest import _load_device

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._run_discovery(_load_device('range_hood'))

    assert coordinator.device_type_name == 'range_hood'
    assert coordinator._unbound_hrefs == []


def test_update_coverage_gap_issue_creates_issue_for_unknown_type(
    hass: HomeAssistant, mock_entry
) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._update_coverage_gap_issue(True, [], 'Test Appliance')

    issue_id = f"device_gap_{mock_entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == 'device_gap'
    assert issue.translation_placeholders == {'device_name': 'Test Appliance'}
    assert issue.is_fixable is False


def test_update_coverage_gap_issue_creates_issue_for_unbound_hrefs(
    hass: HomeAssistant, mock_entry
) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._update_coverage_gap_issue(False, ['/mystery/vs/0'], 'Test Appliance')

    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None


def test_update_coverage_gap_issue_absent_when_fully_covered(
    hass: HomeAssistant, mock_entry
) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    coordinator._update_coverage_gap_issue(False, [], 'Test Appliance')

    issue_id = f"device_gap_{mock_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


def test_update_coverage_gap_issue_clears_previous_issue(
    hass: HomeAssistant, mock_entry
) -> None:
    """A later discovery run with no gap deletes an issue raised earlier."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    issue_id = f"device_gap_{mock_entry.entry_id}"

    coordinator._update_coverage_gap_issue(True, [], 'Test Appliance')
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    coordinator._update_coverage_gap_issue(False, [], 'Test Appliance')
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


# ---------------------------------------------------------------------------
# StateCache / ObserveManager wiring
# ---------------------------------------------------------------------------

async def test_last_resources_backed_by_state_cache(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    """coordinator.last_resources still returns a plain href->rep dict."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    resources = coordinator.last_resources
    assert isinstance(resources, dict)
    assert all(isinstance(v, dict) for v in resources.values())
    assert resources  # fridge fixture is non-empty


def test_logger_is_scoped_to_device_host(
    hass: HomeAssistant, mock_entry
) -> None:
    """Every log line (coordinator's own, DataUpdateCoordinator's internal
    messages, and ObserveManager's) must identify which device it's about —
    a bare module-level logger is shared across every configured device and
    makes multi-device logs ambiguous."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    assert coordinator._log.name.endswith(ENTRY_DATA[CONF_HOST])
    assert coordinator.logger is coordinator._log
    assert coordinator._observe.log is coordinator._log


def test_cache_changes_coalesce_into_a_single_push(
    hass: HomeAssistant, mock_entry
) -> None:
    """A poll/sweep cycle applying many hrefs in a tight loop must not
    schedule one hass.add_job push per href — only one push per burst."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)

    with patch.object(hass, 'add_job') as mock_add_job:
        coordinator._on_cache_changed(True, 'poll')
        coordinator._on_cache_changed(True, 'poll')
        coordinator._on_cache_changed(True, 'poll')

        assert mock_add_job.call_count == 1

        coordinator._push_cache_snapshot()

        coordinator._on_cache_changed(True, 'poll')

        assert mock_add_job.call_count == 2


async def test_enters_poll_mode_when_no_notifies_arrive(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """No fake notifies are sent, so the grace period should time out
    and the coordinator should stay in poll mode (unchanged behavior)."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    assert coordinator.observe_mode == MODE_POLL


async def test_enters_observe_mode_when_hot_warm_hrefs_notify(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs

    # try_enter_observe_mode clears prior notifications before it
    # subscribes, then checks the notified fraction after the grace-period
    # sleep — a notify counts as long as it arrives in that window, not
    # specifically during the sleep itself. The fake session delivers one
    # synchronously from subscribe() for every href it subscribes to (see
    # FakeObserveSession.notify_on_subscribe in conftest.py), which lands
    # it in that window by construction rather than by timing.
    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )

    assert entered is True
    assert coordinator._observe.mode == MODE_OBSERVE


async def test_reconnect_while_observe_mode_downgrades_to_poll(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session, fridge_resources
) -> None:
    """A poll-failure-triggered reconnect while in observe mode must tear
    down the (now stale) observe state rather than leave it dangling on a
    dead session — see _async_update_data's reconnect branch.

    After the reconnect the coordinator should be back in poll mode, which
    hands recovery to the existing _maybe_retry_observe_mode path so it
    re-subscribes on the live session on its next eligible cycle.
    """
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs

    # Get the coordinator into observe mode the same way
    # test_enters_observe_mode_when_hot_warm_hrefs_notify does.
    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    # Age out the notify so recently_notified() is False — a poll failure
    # with a recent notify is now treated as evidence the session is
    # still alive (see test_poll_failure_skips_reconnect_when_push_is_healthy)
    # and would not trigger a reconnect at all. This test covers a
    # genuinely dead channel: no recent push, poll fails, reconnect fires.
    coordinator._observe._last_notify_ts -= PUSH_HEALTH_WINDOW_S + 1

    # Simulate the existing "poll failed, reconnecting" branch: _poll_once
    # fails once (triggering the reconnect/backoff path), then succeeds.
    # The device stops notifying on subscribe, so the immediate resubscribe
    # attempt this now triggers gets nothing back and mode stays 'poll' —
    # this test only asserts the tear-down half of the fix;
    # test_reconnect_from_observe_mode_resubscribes_immediately covers the
    # successful-immediate-resubscribe half.
    fake.notify_on_subscribe = None
    with (
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            side_effect=[RuntimeError('connection lost'), fridge_resources],
        ),
        patch(
            'custom_components.localthings.coordinator.asyncio.sleep',
            new=AsyncMock(),
        ),
    ):
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()

    assert coordinator._observe.mode == MODE_POLL


async def test_poll_timeout_skips_reconnect_when_push_is_healthy(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """A poll (summary GET) TimeoutError with a recent OBSERVE notify must
    not reconnect or downgrade at all — the notify proves the DTLS session
    and its subscriptions are still alive, so the GET failure is just a
    choked blockwise transfer on a slow device, not a dead channel. This
    is the fix for a flaky device (e.g. a slow dishwasher) repeatedly
    tearing down a healthy observe session on nothing but a poll hiccup."""
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs

    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    with (
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            side_effect=TimeoutError('GET /device/0 block 11 timeout'),
        ),
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._close_session',
        ) as mock_close,
    ):
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()

    assert coordinator.observe_mode == MODE_OBSERVE
    mock_close.assert_not_called()


async def test_poll_timeout_reconnects_after_consecutive_limit_even_with_push(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session, fridge_resources
) -> None:
    """Push health only matters at the moment of the failing poll — if
    notifies later go stale too (not supplied here, so recently_notified()
    becomes False once the window elapses) a run of consecutive timeouts
    must still escalate to a reconnect, so a genuinely dead channel isn't
    stuck forever just because it looked healthy once."""
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs

    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    # No notify is recent any more, so every timeout below counts toward
    # the consecutive-timeout limit instead of being deferred.
    coordinator._observe._last_notify_ts -= PUSH_HEALTH_WINDOW_S + 1

    # Call _async_update_data directly rather than via
    # async_request_refresh() — the coordinator's built-in debouncer
    # (REQUEST_REFRESH_DEFAULT_COOLDOWN, real wall-clock seconds) would
    # otherwise coalesce these rapid-fire calls into far fewer than the
    # consecutive-timeout count this test needs to exercise.
    with patch(
        'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
        side_effect=TimeoutError('GET /device/0 block 11 timeout'),
    ):
        for _ in range(coordinator._POLL_TIMEOUT_LIMIT - 1):
            await coordinator._async_update_data()
            assert coordinator.observe_mode == MODE_OBSERVE

    # The final consecutive timeout crosses the limit and triggers the
    # existing reconnect path, which then succeeds and downgrades. The
    # device stops notifying on subscribe, so the resubscribe attempt that
    # follows the reconnect finds nothing and mode stays 'poll'.
    fake.notify_on_subscribe = None
    with (
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            side_effect=[TimeoutError('GET /device/0 block 11 timeout'), fridge_resources],
        ),
        patch(
            'custom_components.localthings.coordinator.asyncio.sleep',
            new=AsyncMock(),
        ),
    ):
        await coordinator._async_update_data()

    assert coordinator.observe_mode == MODE_POLL


async def test_poll_timeout_counter_resets_when_push_is_healthy_again(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """Timeouts accrued during a quiet stretch must not survive into a
    later burst of healthy push — otherwise an intermittently-active
    device could reconnect on old timeouts even though push just proved
    the session alive. The counter means "consecutive timeouts with no
    push activity to vouch for the session," not just "consecutive
    timeouts count.\""""
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs

    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    # Age the notify out so timeouts accrue toward the limit.
    coordinator._observe._last_notify_ts -= PUSH_HEALTH_WINDOW_S + 1
    with patch(
        'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
        side_effect=TimeoutError('GET /device/0 block 11 timeout'),
    ):
        for _ in range(coordinator._POLL_TIMEOUT_LIMIT - 1):
            await coordinator._async_update_data()
    assert coordinator._consecutive_poll_timeouts == coordinator._POLL_TIMEOUT_LIMIT - 1

    # A fresh notify proves push is healthy again — the next timeout must
    # not add to the pre-existing count, and must not reconnect.
    fake.on_notification(hrefs[0], cbor2.dumps({'notified': True}))
    with patch(
        'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
        side_effect=TimeoutError('GET /device/0 block 11 timeout'),
    ):
        await coordinator._async_update_data()

    assert coordinator._consecutive_poll_timeouts == 0
    assert coordinator.observe_mode == MODE_OBSERVE


async def test_reconnect_from_observe_mode_resubscribes_immediately(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session, fridge_resources
) -> None:
    """A reconnect just proved the session is healthy, so the coordinator
    must attempt resubscribe in the same update cycle rather than waiting
    for the 600s poll-mode retry timer (_RECOVERY_RETRY_S) — that timer
    exists to throttle devices that never had observe working at all, not
    ones that just proved their session works."""
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs

    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    # Age out the notify so recently_notified() is False and the poll
    # failure below actually triggers a reconnect (see
    # test_poll_failure_skips_reconnect_when_push_is_healthy for the
    # healthy-push case, which now skips reconnecting entirely).
    coordinator._observe._last_notify_ts -= PUSH_HEALTH_WINDOW_S + 1

    # If a stale retry timer (rather than an immediate resubscribe) were
    # driving recovery, mode would still be 'poll' right after this single
    # refresh call — the 600s gate wouldn't have elapsed yet.
    with (
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            side_effect=[RuntimeError('connection lost'), fridge_resources],
        ),
        patch(
            'custom_components.localthings.coordinator.asyncio.sleep',
            new=AsyncMock(),
        ),
    ):
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()

    assert coordinator.observe_mode == MODE_OBSERVE


async def test_sweep_mismatch_never_downgrades_a_live_observe_session(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """A sweep/cache mismatch is diagnostic-only (see test_observe.py) —
    it must never tear down a still-live OBSERVE session. The sweep
    already re-applies the authoritative state to the cache regardless of
    mode, so a downgrade would only throw away working push coverage on
    every other subscribed href without fixing anything."""
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs
    assert len(hrefs) >= 2

    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    # A sweep result disagreeing with the cache on every subscribed href —
    # previously treated as a device-wide miss and downgraded.
    stale_sweep = {href: {'notified': False} for href in hrefs}

    with patch(
        'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
        return_value=stale_sweep,
    ):
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()

    assert coordinator.observe_mode == MODE_OBSERVE
    # The mismatch is diagnostic-only, but the sweep result still won —
    # the cache reflects the latest authoritative state either way.
    assert coordinator.last_resources[hrefs[0]]['notified'] is False


async def test_sweep_mismatch_forces_subpolls_on_a_live_observe_session(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """A sweep/cache mismatch on a live observe session must not tear
    anything down, but it should trigger extra hot/warm subpolls this
    cycle — the bounded fallback for a channel gone silent without a
    reconnect (e.g. lost internet on an otherwise-live local session)."""
    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    hrefs = coordinator._hot_hrefs + coordinator._warm_hrefs
    assert len(hrefs) >= 2

    fake.notify_on_subscribe = {'notified': True}
    entered = await hass.async_add_executor_job(
        coordinator._observe.try_enter_observe_mode,
        fake, hrefs, 0.02, 0.8,
    )
    assert entered is True
    assert coordinator.observe_mode == MODE_OBSERVE

    stale_sweep = {href: {'notified': False} for href in hrefs}

    with (
        patch(
            'custom_components.localthings.coordinator.LocalThingsCoordinator._poll_once',
            return_value=stale_sweep,
        ),
        patch.object(
            LocalThingsCoordinator, '_run_subpolls', new_callable=AsyncMock,
        ) as mock_subpolls,
    ):
        await coordinator.async_request_refresh()
        await hass.async_block_till_done()

    mock_subpolls.assert_called_once_with(force=True)


async def test_write_marks_href_pending_before_post(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """async_send_command must call mark_write_pending before POSTing so a
    slow-to-settle device can't immediately revert the optimistic write --
    and it must guard the write's actual target href (write_fn's own
    path_segs), not bound_entity.href. Those differ for a composite entity
    like the AC's ClimateDesc (bound to /mode/vs/0, but writing power/temp/
    fan/swing/preset to their own sibling hrefs) -- guarding the bound href
    protected the wrong resource and left the one the entity actually
    displays from unprotected (issues #17/#53)."""
    from custom_components.localthings.registry.discovery import BoundEntity
    from custom_components.localthings.registry.entities import NumberDesc

    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]

    def _write_fn(payload, rep, href):
        return (['some', 'path'], {'value': payload})

    desc = NumberDesc(key='test', field='value', write_fn=_write_fn)
    bound = BoundEntity(href='/test/vs/0', capability=coordinator.bound[0].capability, desc=desc)

    with patch.object(fake, 'subscribe'):
        fake.post = lambda *a, **k: (0x44, b'')
        await coordinator.async_send_command(bound, 5)

    assert coordinator._observe._settle_until.get('/some/path') is not None
    assert coordinator._observe._settle_until.get('/test/vs/0') is None


async def test_send_command_applies_write_optimistically_before_settling(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session,
) -> None:
    """The cache must reflect a write immediately at the write's actual
    target href (write_fn's path_segs) -- not bound_entity.href, which
    differs for a composite entity like the AC's ClimateDesc -- and stay put
    against a stale echo for the rest of the settle window there.

    Applying the optimistic value to bound_entity.href instead is exactly
    how issues #17/#53 survived the original optimistic-apply fix (issue
    #27): the bound href got a harmless (if nonsensical) optimistic merge,
    while the resource the entity actually reads from for its displayed
    state never got one and stayed stale until the next real read of it."""
    from custom_components.localthings.registry.discovery import BoundEntity
    from custom_components.localthings.registry.entities import NumberDesc

    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]

    def _write_fn(payload, rep, href):
        return (['some', 'path'], {'value': payload})

    desc = NumberDesc(key='test', field='value', write_fn=_write_fn)
    bound = BoundEntity(href='/test/vs/0', capability=coordinator.bound[0].capability, desc=desc)

    with patch.object(fake, 'subscribe'):
        fake.post = lambda *a, **k: (0x44, b'')
        await coordinator.async_send_command(bound, 5)

    # The optimistic value is visible right away at the write's real
    # target, not the bound href.
    assert coordinator._cache.get('/some/path') == {'value': 5}
    assert coordinator._cache.get('/test/vs/0') != {'value': 5}

    # A stale update racing in behind the write (e.g. a poll/notify that
    # was already in flight before the PUT) must not clobber it while the
    # settle window is open.
    applied = coordinator._observe.apply('/some/path', {'value': 0}, source='observe')
    assert applied is False
    assert coordinator._cache.get('/some/path') == {'value': 5}


async def test_climate_power_write_applies_to_its_own_href_not_bound_href(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """Regression for issues #17/#53, using the real AC capability. The
    composite climate entity binds /mode/vs/0 (airconditioner.CLIMATE.href),
    but a power command's write_fn (airconditioner._climate_write) targets
    the sibling /power/0 -- the href climate.py's `_is_on()` actually reads.
    The optimistic value and settle guard must land there, not on the bound
    /mode/vs/0, or the entity never sees the write and shows stale state
    until the next unrelated read of /power/0."""
    from custom_components.localthings.registry.capabilities import airconditioner
    from custom_components.localthings.registry.discovery import BoundEntity

    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]

    bound = BoundEntity(
        href=airconditioner.CLIMATE.href,
        capability=coordinator.bound[0].capability,
        desc=airconditioner.CLIMATE.entities[0],
    )

    with patch.object(fake, 'subscribe'):
        fake.post = lambda *a, **k: (0x44, b'')
        await coordinator.async_send_command(bound, ('power', True))

    assert coordinator._cache.get('/power/0') == {'value': True}
    assert coordinator._cache.get(airconditioner.CLIMATE.href) != {'value': True}
    assert coordinator._observe._settle_until.get('/power/0') is not None


class TestRemoteControlEnabled:
    """remote_control_enabled (registry/capabilities/common.py) is the
    single source of truth for the /remotectrl on/off signal, shared by
    the Smart Control binary_sensor descriptors and the coordinator's
    write guard alike."""

    def test_vs_fallback_href_true_is_enabled(self):
        resources = {'/remotectrl/vs/0': {'x.com.samsung.da.remoteControlEnabled': 'true'}}
        assert remote_control_enabled(resources) is True

    def test_vs_fallback_href_false_is_disabled(self):
        resources = {'/remotectrl/vs/0': {'x.com.samsung.da.remoteControlEnabled': 'false'}}
        assert remote_control_enabled(resources) is False

    def test_generic_href_true_is_enabled(self):
        resources = {'/remotectrl/0': {'value': True}}
        assert remote_control_enabled(resources) is True

    def test_generic_href_false_is_disabled(self):
        resources = {'/remotectrl/0': {'value': False}}
        assert remote_control_enabled(resources) is False

    def test_generic_href_wins_when_both_present(self):
        resources = {
            '/remotectrl/0': {'value': True},
            '/remotectrl/vs/0': {'x.com.samsung.da.remoteControlEnabled': 'false'},
        }
        assert remote_control_enabled(resources) is True

    def test_assumes_enabled_when_capability_absent(self):
        assert remote_control_enabled({}) is True


async def test_send_command_blocked_when_remote_control_disabled(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """A device reporting remote control off must reject every write with
    a user-facing message, ahead of write_fn's silent-no-op path and any
    description-level validate_fn."""
    from custom_components.localthings.registry.discovery import BoundEntity
    from custom_components.localthings.registry.entities import NumberDesc

    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._cache.apply_rep(
        '/remotectrl/vs/0',
        {'x.com.samsung.da.remoteControlEnabled': 'false'},
        source='test',
    )

    def _write_fn(payload, rep, href):
        return (['some', 'path'], {'value': payload})

    desc = NumberDesc(key='test', field='value', write_fn=_write_fn)
    bound = BoundEntity(href='/test/vs/0', capability=coordinator.bound[0].capability, desc=desc)

    posted = False

    def _post(*a, **k):
        nonlocal posted
        posted = True
        return (0x44, b'')

    with patch.object(fake, 'subscribe'):
        fake.post = _post
        with pytest.raises(ServiceValidationError, match="Remote control is turned off"):
            await coordinator.async_send_command(bound, 5)

    assert posted is False


async def test_send_command_allowed_when_remote_control_enabled(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """The same write goes through once remote control reports enabled."""
    from custom_components.localthings.registry.discovery import BoundEntity
    from custom_components.localthings.registry.entities import NumberDesc

    fake = mock_coordinator_observe_session
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._cache.apply_rep(
        '/remotectrl/vs/0',
        {'x.com.samsung.da.remoteControlEnabled': 'true'},
        source='test',
    )

    def _write_fn(payload, rep, href):
        return (['some', 'path'], {'value': payload})

    desc = NumberDesc(key='test', field='value', write_fn=_write_fn)
    bound = BoundEntity(href='/test/vs/0', capability=coordinator.bound[0].capability, desc=desc)

    with patch.object(fake, 'subscribe'):
        fake.post = lambda *a, **k: (0x44, b'')
        await coordinator.async_send_command(bound, 5)

    assert coordinator._cache.get('/some/path') == {'value': 5}


async def test_send_command_remote_control_check_precedes_validate_fn(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    """When both a disabled remote-control state and a failing validate_fn
    apply to the same write, the remote-control message wins -- it's the
    more actionable of the two and is checked first."""
    from custom_components.localthings.registry.discovery import BoundEntity
    from custom_components.localthings.registry.entities import SwitchDesc

    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._cache.apply_rep(
        '/remotectrl/vs/0',
        {'x.com.samsung.da.remoteControlEnabled': 'false'},
        source='test',
    )

    def _write_fn(payload, rep, href=None):
        return (['some', 'path'], {'value': payload})

    def _validate_fn(payload, rep, resources):
        return "always rejected"

    desc = SwitchDesc(key='test', field='value', write_fn=_write_fn, validate_fn=_validate_fn)
    bound = BoundEntity(href='/test/vs/0', capability=coordinator.bound[0].capability, desc=desc)

    with pytest.raises(ServiceValidationError, match="Remote control is turned off"):
        await coordinator.async_send_command(bound, 'On')
