"""Late model evidence changes registration in place, never by reloading."""

from unittest.mock import patch

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from tests.conftest import _load_device

from .test_offline_setup import _reachable, _tick, _unreachable

HREF = "/buzzersound/vs/0"
BOOLEAN = {"supportedBuzzerSound": ["On", "Off"], "setBuzzerSound": "Off"}
VOLUME = {"supportedBuzzerSound": ["Volume_Low", "Volume_High"], "setBuzzerSound": "Volume_Low"}


def _buzzer(hass, entry) -> dict[str, bool]:
    """Registered buzzer entities, by platform: whether each is available."""
    found = {}
    for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if row.unique_id.endswith(("_buzzer_sound", "_buzzer_sound_enabled")):
            state = hass.states.get(row.entity_id)
            found[row.domain] = state is not None and state.state != STATE_UNAVAILABLE
    return found


async def _setup(hass, entry, resources):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def _push(hass, coordinator, rep):
    coordinator._observe.apply(HREF, rep, source="observe")
    coordinator._publish_data()
    await hass.async_block_till_done()


@pytest.mark.parametrize("initial", [{}, {"href": HREF}, {"setBuzzerSound": "Off"}, VOLUME])
async def test_late_boolean_evidence_adds_the_switch_in_place_and_saves_it(
    hass, mock_entry, initial
):
    resources = {**_load_device("washer"), HREF: initial}
    with (
        _reachable(resources),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        coordinator = await _setup(hass, mock_entry, resources)
        await _push(hass, coordinator, BOOLEAN)
        assert _buzzer(hass, mock_entry)["switch"] is True
        assert _buzzer(hass, mock_entry).get("select", False) is False
        saved = await coordinator._snapshot_store.async_load()
        assert {k: v for k, v in saved["resources"][HREF].items() if k != "href"} == BOOLEAN
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
    reload.assert_not_called()
    with _unreachable():
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    switch = next(b for b in coordinator.bound if b.desc.key == "buzzer_sound_enabled")
    assert coordinator.registrable(switch)


@pytest.mark.parametrize(
    "initial,after,changed",
    [
        (BOOLEAN, BOOLEAN, False),
        (BOOLEAN, {"setBuzzerSound": "Off"}, False),
        (BOOLEAN, VOLUME, True),
        ({"setBuzzerSound": "Off"}, VOLUME, True),
    ],
)
async def test_only_complete_changed_choices_change_registration(
    hass, mock_entry, initial, after, changed
):
    resources = {**_load_device("washer"), HREF: initial}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        before = _buzzer(hass, mock_entry)
        with patch.object(coordinator, "_async_rebank") as rebank:
            await _push(hass, coordinator, after)
        assert (_buzzer(hass, mock_entry) != before) is changed
        assert rebank.called is changed


async def test_evidence_during_platform_forwarding_is_not_missed(hass, mock_entry):
    resources = {**_load_device("washer"), HREF: {"setBuzzerSound": "Off"}}
    forward = hass.config_entries.async_forward_entry_setups

    async def updating_forward(entry, platforms):
        await forward(entry, platforms)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator._observe.apply(HREF, BOOLEAN, source="observe")

    with (
        _reachable(resources),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=updating_forward
        ),
    ):
        await _setup(hass, mock_entry, resources)
    assert _buzzer(hass, mock_entry)["switch"] is True


async def test_unload_cancels_a_pending_snapshot_write(hass, mock_entry):
    import asyncio

    resources = {**_load_device("washer"), HREF: VOLUME}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked_save(data):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with patch.object(coordinator._snapshot_store, "async_save", side_effect=blocked_save):
            coordinator._observe.apply(HREF, BOOLEAN, source="observe")
            await started.wait()
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()
            assert cancelled.is_set()


async def test_failed_snapshot_write_still_registers_in_place(hass, mock_entry):
    resources = {**_load_device("washer"), HREF: VOLUME}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        with patch.object(
            coordinator._snapshot_store, "async_save", side_effect=OSError("disk unavailable")
        ):
            await _push(hass, coordinator, BOOLEAN)
            await hass.async_block_till_done(wait_background_tasks=True)
        assert _buzzer(hass, mock_entry) == {"select": False, "switch": True}


async def test_decisive_rep_is_captured_before_background_save(hass, mock_entry):
    resources = {**_load_device("washer"), HREF: VOLUME}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        create_task = mock_entry.async_create_background_task

        def deferred_task(hass, target, name):
            return create_task(hass, target, name, eager_start=False)

        with patch.object(mock_entry, "async_create_background_task", side_effect=deferred_task):
            coordinator._observe.apply(HREF, BOOLEAN, source="observe")
            coordinator._update_late_decisions()
            # A partial response may replace the cache before the save task runs.
            coordinator._observe.apply(
                HREF, {"setBuzzerSound": "Off", "supportedBuzzerSound": []}, source="observe"
            )
            await hass.async_block_till_done()
            saved = await coordinator._snapshot_store.async_load()
            assert saved["resources"][HREF] == BOOLEAN


async def test_partial_first_recovery_preserves_snapshot_platform(hass, mock_entry):
    resources = {**_load_device("washer"), HREF: BOOLEAN}
    with _reachable(resources):
        await _setup(hass, mock_entry, resources)
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
    with _unreachable():
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    partial = {**resources, HREF: {"setBuzzerSound": "Off"}}
    with _reachable(partial):
        await _tick(hass)
        saved = await coordinator._snapshot_store.async_load()
        assert saved["resources"][HREF] == BOOLEAN
    assert _buzzer(hass, mock_entry)["switch"] is True


@pytest.mark.parametrize("initial", [{"href": HREF}, {"setBuzzerSound": "Off"}])
async def test_cold_start_registers_from_saved_complete_evidence(hass, mock_entry, initial):
    resources = {**_load_device("washer"), HREF: dict(initial)}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        await _push(hass, coordinator, BOOLEAN)
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        resources[HREF] = dict(initial)
        # Exercise a reachable cold start rather than setup's snapshot-first path.
        with patch.object(LocalThingsCoordinator, "async_rehydrate", return_value=False):
            coordinator = await _setup(hass, mock_entry, resources)
        assert "switch" in _buzzer(hass, mock_entry)
        with patch.object(coordinator, "_async_rebank") as rebank:
            await _push(hass, coordinator, BOOLEAN)
        rebank.assert_not_called()
        assert _buzzer(hass, mock_entry)["switch"] is True


@pytest.mark.parametrize("initial", [{}, {"href": HREF}])
async def test_provisional_volume_select_is_added_in_place(hass, mock_entry, initial):
    resources = {**_load_device("washer"), HREF: dict(initial)}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        await _push(hass, coordinator, VOLUME)
        assert _buzzer(hass, mock_entry)["select"] is True


@pytest.mark.parametrize("initial,after", [(VOLUME, BOOLEAN), (BOOLEAN, VOLUME)])
async def test_complete_buzzer_change_wins_over_missing_finish_fields(
    hass, mock_entry, initial, after
):
    finish = {"supportedFinishSound": ["On", "Off"], "setFinishSound": "Off"}
    resources = {**_load_device("washer"), HREF: {**initial, **finish}}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        before = _buzzer(hass, mock_entry)
        await _push(hass, coordinator, dict(after))
        assert _buzzer(hass, mock_entry) != before
        snapshot = await coordinator._snapshot_store.async_load()
        assert snapshot["resources"][HREF] == {**after, **finish}


async def test_complete_finish_fields_preserve_undecided_buzzer(hass, mock_entry):
    finish = {"supportedFinishSound": ["On", "Off"], "setFinishSound": "Off"}
    resources = {**_load_device("washer"), HREF: {**BOOLEAN, **finish}}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        before = _buzzer(hass, mock_entry)
        await _push(hass, coordinator, {"setBuzzerSound": "Off", **finish})
        assert _buzzer(hass, mock_entry) == before


@pytest.mark.parametrize(
    "initial,partial,changed",
    [
        # A new vocabulary is the board changing its buzzer.
        (BOOLEAN, {"supportedBuzzerSound": VOLUME["supportedBuzzerSound"]}, True),
        (VOLUME, {"supportedBuzzerSound": BOOLEAN["supportedBuzzerSound"]}, True),
        # A state outside the known vocabulary decides nothing.
        (VOLUME, {"setBuzzerSound": "On"}, False),
    ],
)
async def test_partial_buzzer_update_changes_registration_only_on_new_vocabulary(
    hass, mock_entry, initial, partial, changed
):
    resources = {**_load_device("washer"), HREF: dict(initial)}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        before = _buzzer(hass, mock_entry)
        await _push(hass, coordinator, dict(partial))
        assert (_buzzer(hass, mock_entry) != before) is changed


async def test_field_missing_from_a_populated_rep_is_not_filled_from_snapshot(hass, mock_entry):
    finish = {"supportedFinishSound": ["On", "Off"], "setFinishSound": "Off"}
    resources = {**_load_device("washer"), HREF: {**BOOLEAN, **finish}}
    with _reachable(resources):
        await _setup(hass, mock_entry, resources)
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        # The board stops reporting its buzzer; only a stub or {} may borrow
        # the snapshot's evidence, so the switch must not outlive the field.
        resources[HREF] = dict(finish)
        with patch.object(LocalThingsCoordinator, "async_rehydrate", return_value=False):
            coordinator = await _setup(hass, mock_entry, resources)
        switch = next(b for b in coordinator.bound if b.desc.key == "buzzer_sound_enabled")
        assert not _is_included(switch, coordinator)
        saved = await coordinator._snapshot_store.async_load()
        assert "setBuzzerSound" not in saved["resources"][HREF]


async def test_undecided_first_read_never_reloads(hass, mock_entry):
    """{} is a confirmed empty answer, so it borrows nothing from the
    snapshot. A board whose first read is always {} once reloaded on every
    start; the switch is now added in place each time instead."""
    resources = {**_load_device("washer"), HREF: {}}
    with (
        _reachable(resources),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        for _ in range(2):
            with patch.object(LocalThingsCoordinator, "async_rehydrate", return_value=False):
                coordinator = await _setup(hass, mock_entry, resources)
            await _push(hass, coordinator, BOOLEAN)
            assert _buzzer(hass, mock_entry)["switch"] is True
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()
    reload.assert_not_called()


async def test_watcher_ignores_gates_without_late_evidence(hass, mock_entry):
    resources = {**_load_device("washer"), HREF: dict(VOLUME)}
    with _reachable(resources):
        coordinator = await _setup(hass, mock_entry, resources)
        late = {b.desc.key for b in coordinator._late_bound()}
        assert late == {"buzzer_sound", "buzzer_sound_enabled"}
