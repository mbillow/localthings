"""Non-heating oven/range writes work with Remote Control off.

The SmartThings app lets the lamp, the sound and the 120-hour energy
saving be changed without Smart Control (issues #183, #500). These tests
pin which writes waive Remote Control, that each waived write is still
read back, and that everything that heats or starts a cook stays refused.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator, _payload_present_in
from custom_components.localthings.registry.capabilities import microwave, oven, range_hood
from custom_components.localthings.registry.discovery import BoundEntity
from tests.conftest import _load_device

_WAIVED = ("lamp", "sound", "energy_saving")
_STILL_GATED = ("oven_mode", "fast_preheat", "natural_steam", "cooktop_on_alert")
_OPTIONS = [
    "SettingPossible_0",
    "UpperLamp_Off",
    "Sound_On",
    "Sabbath_Off",
    "EnergySaving_On",
    "BurnerOnAlert_Off",
]
_PREFIX = {"lamp": "UpperLamp", "sound": "Sound", "energy_saving": "EnergySaving"}


def _mode_desc(key):
    return next(d for d in oven.OVEN_MODE.entities if d.key == key)


@pytest.mark.parametrize("key", _WAIVED)
def test_non_heating_switches_waive_remote_control(key) -> None:
    assert _mode_desc(key).needs_remote_control is False


@pytest.mark.parametrize("key", _STILL_GATED)
def test_other_mode_writes_still_need_remote_control(key) -> None:
    assert _mode_desc(key).needs_remote_control is True


def test_cooking_writes_still_need_remote_control() -> None:
    assert oven.OVEN_SETPOINT.entities[0].needs_remote_control is True
    assert oven.START_COOKING_BUTTON.needs_remote_control is True
    cook_time = next(d for d in oven.OVEN_OPERATIONAL_STATE.entities if d.key == "cook_time")
    assert cook_time.needs_remote_control is True


def test_microwave_lamp_writes_waive_remote_control() -> None:
    lamp_writers = (
        microwave._lamp_write,
        microwave._lamp_level_write,
        microwave._lamp_brightness_write,
    )
    waived = [d for d in microwave.MICROWAVE_MODE.entities if d.write_fn in lamp_writers]
    assert len(waived) == len(lamp_writers)
    assert all(d.needs_remote_control is False for d in waived)


def test_microwave_cooking_mode_still_needs_remote_control() -> None:
    mode = next(d for d in microwave.MICROWAVE_MODE.entities if d.key == "cooking_mode")
    assert mode.needs_remote_control is True


def test_hood_fan_waives_remote_control() -> None:
    fan = next(d for d in range_hood.HOOD_FAN.entities if d.key == "fan")
    assert fan.needs_remote_control is False


def test_options_token_counts_as_taken_when_merged_into_the_array() -> None:
    readback = {"x.com.samsung.da.options": ["SettingPossible_0", "UpperLamp_On", "Sound_On"]}
    assert _payload_present_in({"x.com.samsung.da.options": ["UpperLamp_On"]}, readback)
    assert not _payload_present_in({"x.com.samsung.da.options": ["UpperLamp_Off"]}, readback)


async def _oven_with_remote_control_off(hass, mock_entry) -> LocalThingsCoordinator:
    resources = _load_device("oven")
    resources["/remotectrl/vs/0"] = {"x.com.samsung.da.remoteControlEnabled": "false"}
    resources["/mode/vs/0"].update(
        {
            "x.com.samsung.da.modes": ["NoOperation"],
            "x.com.samsung.da.options": list(_OPTIONS),
        }
    )
    with patch.object(LocalThingsCoordinator, "_poll_once", return_value=resources):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    return coordinator


@pytest.mark.parametrize("key", _WAIVED)
async def test_waived_switch_is_sent_and_read_back_with_remote_control_off(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session, key
) -> None:
    fake = mock_coordinator_observe_session
    coordinator = await _oven_with_remote_control_off(hass, mock_entry)
    bound = BoundEntity(href="/mode/vs/0", capability=oven.OVEN_MODE, desc=_mode_desc(key))
    token = f"{_PREFIX[key]}_On"
    after = [o for o in _OPTIONS if not o.startswith(_PREFIX[key] + "_")] + [token]
    written = []
    mode_rep = dict(coordinator.cached_resource("/mode/vs/0") or {})

    with (
        patch.object(fake, "subscribe"),
        patch.object(
            fake,
            "read",
            create=True,
            return_value=(0x45, {**mode_rep, "x.com.samsung.da.options": after}),
        ) as read,
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0.0),
    ):
        fake.write = lambda *a, **k: written.append(a) or (0x44, None)
        await coordinator.async_send_command(bound, "On")

    assert written[0][0] == ["mode", "vs", "0"]
    assert written[0][1] == {"x.com.samsung.da.options": [token]}
    # Sub-polls read /mode/vs/0 too and can land in the same window; the
    # confirm is the one read made with the write timeout.
    confirms = [
        c.args[0]
        for c in read.call_args_list
        if c.kwargs.get("timeout") == LocalThingsCoordinator._POST_TIMEOUT_S
    ]
    assert confirms == [["mode", "vs", "0"]]


async def test_waived_switch_the_oven_dropped_is_reported(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    fake = mock_coordinator_observe_session
    coordinator = await _oven_with_remote_control_off(hass, mock_entry)
    bound = BoundEntity(href="/mode/vs/0", capability=oven.OVEN_MODE, desc=_mode_desc("lamp"))
    mode_rep = dict(coordinator.cached_resource("/mode/vs/0") or {})

    with (
        patch.object(fake, "subscribe"),
        patch.object(
            fake,
            "read",
            create=True,
            return_value=(0x45, {**mode_rep, "x.com.samsung.da.options": _OPTIONS}),
        ),
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0.0),
    ):
        fake.write = lambda *a, **k: (0x44, None)
        with pytest.raises(HomeAssistantError) as err:
            await coordinator.async_send_command(bound, "On")

    assert err.value.translation_key == "command_not_confirmed"


async def test_cooking_commands_still_refused_with_remote_control_off(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    fake = mock_coordinator_observe_session
    coordinator = await _oven_with_remote_control_off(hass, mock_entry)
    # An active cavity sends edits to the device instead of staging an idle cook.
    coordinator._cache.apply_rep(
        "/operational/state/vs/0", {"x.com.samsung.da.state": "Run"}, source="test"
    )
    coordinator._cache.apply_rep(
        "/temperatures/vs/0",
        {"x.com.samsung.da.items": [{"x.com.samsung.da.id": "0", "x.com.samsung.da.desired": "0"}]},
        source="test",
    )
    setpoint = BoundEntity(
        href="/temperatures/vs/0",
        capability=oven.OVEN_SETPOINT,
        desc=oven.OVEN_SETPOINT.entities[0],
    )
    start = BoundEntity(
        href="/operational/state/vs/0",
        capability=oven.OVEN_OPERATIONAL_STATE,
        desc=oven.START_COOKING_BUTTON,
    )
    fast_preheat = BoundEntity(
        href="/mode/vs/0", capability=oven.OVEN_MODE, desc=_mode_desc("fast_preheat")
    )
    written = []

    with patch.object(fake, "subscribe"):
        fake.write = lambda *a, **k: written.append(a) or (0x44, None)
        for bound, payload in ((setpoint, 200), (start, None), (fast_preheat, "On")):
            with pytest.raises(ServiceValidationError) as err:
                await coordinator.async_send_command(bound, payload)
            assert err.value.translation_key == "remote_control_disabled"

    assert written == []
