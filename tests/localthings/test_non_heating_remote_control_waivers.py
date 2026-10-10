"""Non-heating oven/range writes work with Remote Control off.

The SmartThings app lets the lamp, the sound and the 120-hour energy
saving be changed without Smart Control (issues #183, #500). These tests
pin which writes waive Remote Control, that each waived write is still
read back, and that everything that heats or starts a cook stays refused.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator, _payload_present_in
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.capabilities.common import remote_control_enabled
from custom_components.localthings.registry.discovery import BoundEntity

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
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


def test_options_token_counts_as_taken_when_merged_into_the_array() -> None:
    readback = {"x.com.samsung.da.options": ["SettingPossible_0", "UpperLamp_On", "Sound_On"]}
    assert _payload_present_in({"x.com.samsung.da.options": ["UpperLamp_On"]}, readback)
    assert not _payload_present_in({"x.com.samsung.da.options": ["UpperLamp_Off"]}, readback)


def test_fridge_fixtures_report_no_remote_control_lock() -> None:
    """Fridges never carry /remotectrl, so none of their writes is gated --
    the SmartThings behaviour already, with nothing to waive."""
    fridges = sorted(FIXTURES_DIR.glob("refrigerator_*.json"))
    assert fridges
    for path in fridges:
        text = path.read_text(encoding="utf-8")
        assert "/remotectrl" not in text, path.name
    assert remote_control_enabled({}) is True


async def _oven_with_remote_control_off(hass, mock_entry) -> LocalThingsCoordinator:
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator: LocalThingsCoordinator = hass.data[DOMAIN][mock_entry.entry_id]
    coordinator._cache.apply_rep(
        "/remotectrl/vs/0", {"x.com.samsung.da.remoteControlEnabled": "false"}, source="test"
    )
    coordinator._cache.apply_rep(
        "/mode/vs/0",
        {"x.com.samsung.da.modes": ["NoOperation"], "x.com.samsung.da.options": list(_OPTIONS)},
        source="test",
    )
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

    with (
        patch.object(fake, "subscribe"),
        patch.object(
            fake, "read", create=True, return_value=(0x45, {"x.com.samsung.da.options": after})
        ) as read,
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0.0),
    ):
        fake.write = lambda *a, **k: written.append(a) or (0x44, None)
        await coordinator.async_send_command(bound, "On")

    assert written[0][0] == ["mode", "vs", "0"]
    assert written[0][1] == {"x.com.samsung.da.options": [token]}
    # Background door reads can land in the same window; only the confirm matters.
    assert [c.args[0] for c in read.call_args_list].count(["mode", "vs", "0"]) == 1


async def test_waived_switch_the_oven_dropped_is_reported(
    hass: HomeAssistant, mock_entry, mock_coordinator_observe_session
) -> None:
    fake = mock_coordinator_observe_session
    coordinator = await _oven_with_remote_control_off(hass, mock_entry)
    bound = BoundEntity(href="/mode/vs/0", capability=oven.OVEN_MODE, desc=_mode_desc("lamp"))

    with (
        patch.object(fake, "subscribe"),
        patch.object(
            fake, "read", create=True, return_value=(0x45, {"x.com.samsung.da.options": _OPTIONS})
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
