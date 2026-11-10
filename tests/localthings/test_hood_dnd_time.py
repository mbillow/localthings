"""DND service writes keep both UTC clock halves on one local offset."""

import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from tests.conftest import _load_device

from .test_offline_setup import _reachable

HREF = "/airlevelcheck/vs/0"
FIELD = "x.com.samsung.da.periodicSensingSkipTime"


@pytest.mark.parametrize(
    "date,zone,want",
    [
        ("2026-11-01T03:00:00+00:00", "America/Los_Angeles", "05001200"),
        ("2026-03-08T04:00:00+00:00", "America/Los_Angeles", "06001300"),
        ("2026-11-01T20:00:00+00:00", "UTC", "22000500"),
        ("2026-11-01T20:00:00+00:00", "Asia/Kolkata", "16302330"),
        ("2026-11-01T20:00:00+00:00", "America/Phoenix", "05001200"),
    ],
)
@pytest.mark.parametrize("order", [("start", "end"), ("end", "start")])
@pytest.mark.parametrize("raw", ["00000000", "05001200"])
async def test_dnd_services_keep_seven_hour_window(
    hass, mock_entry, monkeypatch, date, zone, want, order, raw
):
    now = datetime.datetime.fromisoformat(date).astimezone(ZoneInfo(zone))
    resources = _load_device("range_hood")
    # Keep the entities present even when a firmware value is null.
    resources[HREF][FIELD] = raw if raw is not None else ""
    sent = []

    async def send(self, path, body, *args, **kwargs):
        assert path == ["airlevelcheck", "vs", "0"]
        assert set(body) == {FIELD}
        sent.append(body[FIELD])
        resources[HREF].update(body)
        return True

    with (
        _reachable(resources),
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0),
        patch.object(LocalThingsCoordinator, "_async_put", send),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with (
            patch.object(dt_util, "now", return_value=now),
            patch.object(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo(zone)),
        ):
            for part in order:
                bound = next(
                    b for b in coordinator.bound if b.desc.key == f"hood_do_not_disturb_{part}"
                )
                entity_id = registry.async_get_entity_id(
                    "time", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_{bound.desc.key}"
                )
                assert entity_id is not None
                await hass.services.async_call(
                    "time",
                    "set_value",
                    {"entity_id": entity_id, "time": "22:00:00" if part == "start" else "05:00:00"},
                    blocking=True,
                )
            await hass.async_block_till_done()
            assert sent[-1] == want
            assert coordinator.entity_rep(HREF)[FIELD] == want
            for part, want_state in (("start", "22:00:00"), ("end", "05:00:00")):
                entity_id = registry.async_get_entity_id(
                    "time", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_hood_do_not_disturb_{part}"
                )
                assert hass.states.get(entity_id).state == want_state


@pytest.mark.parametrize("board,extra", [("AHD-WW-TP1-22-COMMON", True), ("UNVERIFIED", False)])
async def test_mode_service_uses_board_evidence(hass, mock_entry, board, extra):
    resources = _load_device("range_hood")
    resources["/information/vs/0"]["x.com.samsung.da.modelNum"] = board
    resources[HREF]["x.com.samsung.da.autoExeState"] = "Off"
    written = []

    async def send(self, path, body, *args, **kwargs):
        written.append(body)
        resources[HREF].update(body)
        return True

    with (
        _reachable(resources),
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0),
        patch.object(LocalThingsCoordinator, "_async_put", send),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        bound = next(b for b in coordinator.bound if b.desc.key == "automatic_ventilation_state")
        entity_id = er.async_get(hass).async_get_entity_id(
            "select", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_{bound.desc.key}"
        )
        state = hass.states.get(entity_id)
        assert state.state in state.attributes["options"]
        assert ("sensing" in state.attributes["options"]) is extra
        if extra:
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": entity_id, "option": "sensing"},
                blocking=True,
            )
            assert written == [{"x.com.samsung.da.autoExeState": "Sensing"}]
        else:
            assert await coordinator.async_send_command(bound, "Sensing") is False
            assert not written


@pytest.mark.parametrize(
    "date,picked,want,start_state",
    [
        ("2026-11-01T09:15:00+00:00", "01:30:00", "09301300", "01:30:00"),
        ("2026-03-08T08:15:00+00:00", "02:30:00", "10301200", "03:30:00"),
    ],
)
@pytest.mark.parametrize("order", [("start", "end"), ("end", "start")])
async def test_gap_and_fold_window_is_independent_of_edit_order(
    hass, mock_entry, date, picked, want, start_state, order
):
    resources = _load_device("range_hood")
    resources[HREF][FIELD] = "00000000"
    now = datetime.datetime.fromisoformat(date).astimezone(ZoneInfo("America/Los_Angeles"))
    written = []

    async def send(self, path, body, *args, **kwargs):
        written.append(body[FIELD])
        resources[HREF].update(body)
        return True

    with (
        _reachable(resources),
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0),
        patch.object(LocalThingsCoordinator, "_async_put", send),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        registry = er.async_get(hass)
        with (
            patch.object(dt_util, "now", return_value=now),
            patch.object(dt_util, "DEFAULT_TIME_ZONE", now.tzinfo),
        ):
            for part in order:
                entity_id = registry.async_get_entity_id(
                    "time", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_hood_do_not_disturb_{part}"
                )
                await hass.services.async_call(
                    "time",
                    "set_value",
                    {"entity_id": entity_id, "time": picked if part == "start" else "05:00:00"},
                    blocking=True,
                )
            await hass.async_block_till_done()
            assert written[-1] == want
            for part, state in (("start", start_state), ("end", "05:00:00")):
                entity_id = registry.async_get_entity_id(
                    "time", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_hood_do_not_disturb_{part}"
                )
                assert hass.states.get(entity_id).state == state


async def test_reported_sensing_remains_restorable_after_mode_change(hass, mock_entry):
    resources = _load_device("range_hood")
    resources["/information/vs/0"]["x.com.samsung.da.modelNum"] = "UNVERIFIED"
    resources[HREF]["x.com.samsung.da.autoExeState"] = "Sensing"
    resources[HREF]["x.com.samsung.da.supportedAutoExeState"] = ["Alarm"]
    written = []

    async def send(self, path, body, *args, **kwargs):
        written.append(body)
        resources[HREF].update(body)
        return True

    with (
        _reachable(resources),
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0),
        patch.object(LocalThingsCoordinator, "_async_put", send),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        entity_id = er.async_get(hass).async_get_entity_id(
            "select", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_automatic_ventilation_state"
        )
        for option in ("alarm", "sensing"):
            await hass.services.async_call(
                "select", "select_option", {"entity_id": entity_id, "option": option}, blocking=True
            )
        assert written == [
            {"x.com.samsung.da.autoExeState": "Alarm"},
            {"x.com.samsung.da.autoExeState": "Sensing"},
        ]
        assert (
            "_localthings_sensing_observed"
            not in coordinator.device_resources(coordinator.bound[0].subdevice)[HREF]
        )


async def test_only_device_reports_teach_sensing_support(hass, mock_entry):
    resources = _load_device("range_hood")
    resources["/information/vs/0"]["x.com.samsung.da.modelNum"] = "UNVERIFIED"
    resources[HREF]["x.com.samsung.da.autoExeState"] = "Alarm"
    resources[HREF]["x.com.samsung.da.supportedAutoExeState"] = ["Alarm"]
    with _reachable(resources):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        bound = next(b for b in coordinator.bound if b.desc.key == "automatic_ventilation_state")
        for source, retained in (("optimistic", False), ("poll", True)):
            for mode in ("Sensing", "Alarm"):
                coordinator._observe.apply(
                    HREF, {"x.com.samsung.da.autoExeState": mode}, source=source
                )
            view = coordinator.canonical_resources(bound.subdevice)
            assert ("Sensing" in bound.desc.options(view)) is retained
            assert (
                bound.desc.write_fn("Sensing", coordinator.entity_rep(HREF), HREF, view) is not None
            ) is retained
