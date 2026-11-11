"""Late model evidence must replace provisional platform registration."""

from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from tests.conftest import _load_device

from .test_offline_setup import _reachable, _tick, _unreachable

HREF = "/buzzersound/vs/0"
BOOLEAN = {"supportedBuzzerSound": ["On", "Off"], "setBuzzerSound": "Off"}
VOLUME = {"supportedBuzzerSound": ["Volume_Low", "Volume_High"], "setBuzzerSound": "Volume_Low"}


@pytest.mark.parametrize("initial", [{}, {"href": HREF}, {"setBuzzerSound": "Off"}, VOLUME])
async def test_late_boolean_evidence_reloads_once_and_saves_snapshot(hass, mock_entry, initial):
    resources = _load_device("washer")
    resources[HREF] = initial
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_called_once_with(mock_entry.entry_id)
        saved = await coordinator._snapshot_store.async_load()
        assert saved["resources"][HREF] == BOOLEAN
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
    with _unreachable():
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    assert any(
        row.domain == "switch" and row.unique_id.endswith("_buzzer_sound_enabled")
        for row in er.async_entries_for_config_entry(er.async_get(hass), mock_entry.entry_id)
    )


@pytest.mark.parametrize(
    "initial,after,expected",
    [
        (BOOLEAN, BOOLEAN, 0),
        (BOOLEAN, {"setBuzzerSound": "Off"}, 0),
        (BOOLEAN, VOLUME, 1),
        ({"setBuzzerSound": "Off"}, VOLUME, 1),
    ],
)
async def test_only_complete_changed_choices_reload(hass, mock_entry, initial, after, expected):
    resources = _load_device("washer")
    resources[HREF] = initial
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(HREF, after, source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            assert reload.call_count == expected


async def test_evidence_during_platform_forwarding_is_not_missed(hass, mock_entry):
    resources = _load_device("washer")
    resources[HREF] = {"setBuzzerSound": "Off"}
    forward = hass.config_entries.async_forward_entry_setups

    async def updating_forward(entry, platforms):
        await forward(entry, platforms)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")

    with (
        _reachable(resources),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=updating_forward
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        reload.assert_called_once_with(mock_entry.entry_id)


async def test_unload_cancels_pending_snapshot_and_reload(hass, mock_entry):
    import asyncio

    resources = _load_device("washer")
    resources[HREF] = VOLUME
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked_save(data):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with (
            patch.object(coordinator._snapshot_store, "async_save", side_effect=blocked_save),
            patch.object(hass.config_entries, "async_schedule_reload") as reload,
        ):
            coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")
            await started.wait()
            assert await hass.config_entries.async_unload(mock_entry.entry_id)
            await hass.async_block_till_done()
            assert cancelled.is_set()
            reload.assert_not_called()


async def test_failed_snapshot_defers_reload_until_evidence_can_be_saved(hass, mock_entry):
    resources = _load_device("washer")
    resources[HREF] = VOLUME
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            with patch.object(
                coordinator._snapshot_store, "async_save", side_effect=OSError("disk unavailable")
            ):
                coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")
                await hass.async_block_till_done()
                reload.assert_not_called()
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_called_once_with(mock_entry.entry_id)


async def test_decisive_rep_is_captured_before_background_save(hass, mock_entry):
    resources = _load_device("washer")
    resources[HREF] = VOLUME
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        create_task = mock_entry.async_create_background_task

        def deferred_task(hass, target, name):
            return create_task(hass, target, name, eager_start=False)

        with (
            patch.object(mock_entry, "async_create_background_task", side_effect=deferred_task),
            patch.object(hass.config_entries, "async_schedule_reload") as reload,
        ):
            coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")
            coordinator._check_entity_inclusion()
            # A partial response may replace the cache before the save task runs.
            coordinator._cache.apply_rep(
                HREF, {"setBuzzerSound": "Off", "supportedBuzzerSound": []}, source="observe"
            )
            await hass.async_block_till_done()
            saved = await coordinator._snapshot_store.async_load()
            assert saved["resources"][HREF] == BOOLEAN
            reload.assert_called_once_with(mock_entry.entry_id)


async def test_partial_first_recovery_preserves_snapshot_platform(hass, mock_entry):
    resources = _load_device("washer")
    resources[HREF] = BOOLEAN
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
    with _unreachable():
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][mock_entry.entry_id]
    partial = {**resources, HREF: {"setBuzzerSound": "Off"}}
    with _reachable(partial), patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _tick(hass)
        reload.assert_not_called()
        saved = await coordinator._snapshot_store.async_load()
        assert saved["resources"][HREF] == BOOLEAN


@pytest.mark.parametrize("initial", [{}, {"href": HREF}, {"setBuzzerSound": "Off"}])
async def test_online_reload_retains_complete_evidence(hass, mock_entry, initial):
    resources = {**_load_device("washer"), HREF: dict(initial)}
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_called_once_with(mock_entry.entry_id)
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()
        resources[HREF] = dict(initial)
        # Exercise a reachable cold start rather than setup's snapshot-first path.
        with (
            patch.object(LocalThingsCoordinator, "async_rehydrate", return_value=False),
            patch.object(hass.config_entries, "async_schedule_reload") as reload,
        ):
            assert await hass.config_entries.async_setup(mock_entry.entry_id)
            await hass.async_block_till_done()
            coordinator = hass.data[DOMAIN][mock_entry.entry_id]
            registry = er.async_get(hass)
            entity_id = registry.async_get_entity_id(
                "switch", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_buzzer_sound_enabled"
            )
            assert entity_id is not None
            assert hass.states.get(entity_id) is not None
            coordinator._cache.apply_rep(HREF, BOOLEAN, source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_not_called()


@pytest.mark.parametrize("initial", [{}, {"href": HREF}])
async def test_provisional_volume_select_needs_no_reload(hass, mock_entry, initial):
    resources = {**_load_device("washer"), HREF: dict(initial)}
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(HREF, VOLUME, source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_not_called()


@pytest.mark.parametrize("initial,after", [(VOLUME, BOOLEAN), (BOOLEAN, VOLUME)])
async def test_complete_buzzer_change_wins_over_missing_finish_fields(
    hass, mock_entry, initial, after
):
    resources = _load_device("washer")
    resources[HREF] = {
        **initial,
        "supportedFinishSound": ["On", "Off"],
        "setFinishSound": "Off",
    }
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(HREF, dict(after), source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_called_once_with(mock_entry.entry_id)
            snapshot = await coordinator._snapshot_store.async_load()
            assert snapshot["resources"][HREF] == {
                **after,
                "supportedFinishSound": ["On", "Off"],
                "setFinishSound": "Off",
            }


async def test_complete_finish_fields_preserve_undecided_buzzer(hass, mock_entry):
    finish = {"supportedFinishSound": ["On", "Off"], "setFinishSound": "Off"}
    resources = {**_load_device("washer"), HREF: {**BOOLEAN, **finish}}
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(
                HREF, {"setBuzzerSound": "Off", **finish}, source="observe"
            )
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_not_called()


@pytest.mark.parametrize(
    "initial,partial",
    [
        (BOOLEAN, {"supportedBuzzerSound": VOLUME["supportedBuzzerSound"]}),
        (VOLUME, {"supportedBuzzerSound": BOOLEAN["supportedBuzzerSound"]}),
        (VOLUME, {"setBuzzerSound": "On"}),
    ],
)
async def test_incompatible_partial_pair_preserves_registration(hass, mock_entry, initial, partial):
    resources = {**_load_device("washer"), HREF: dict(initial)}
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            coordinator._cache.apply_rep(HREF, dict(partial), source="observe")
            coordinator._publish_data()
            await hass.async_block_till_done()
            reload.assert_not_called()
