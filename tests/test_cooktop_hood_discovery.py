"""The captured unpaired hood slot must not create a second cooktop."""

import json
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.localthings import async_remove_config_entry_device
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.registry.subdevices import MAIN, Subdevice
from tests.conftest import _load_device
from tests.test_subdevice_discovery import _coordinator, _discover_with


def _hood_resources():
    return json.loads(
        (Path(__file__).parent / "fixtures/legacy_cooktop_empty_hood.json").read_text()
    )


async def _discover_cooktop(coordinator, hood):
    subdevice = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    seed = [{"href": subdevice.to_actual(href), "rep": rep} for href, rep in hood.items()]
    await _discover_with(
        coordinator,
        _load_device("cooktop_tp6x_ct_16k_8888"),
        [],
        {"/device/1": seed},
    )


async def test_empty_hood_is_skipped_and_existing_device_can_be_deleted(hass):
    coordinator = _coordinator(hass)
    await _discover_cooktop(coordinator, _hood_resources())
    assert coordinator.subdevices == []
    assert [s.subdevice.key for s in coordinator._skipped_subdevices] == ["1"]
    assert all(b.subdevice == MAIN for b in coordinator.bound)
    assert any(b.desc.key == "burner_3_pan_size" for b in coordinator.bound)
    assert "/mode/vs/1" not in coordinator._hot_hrefs
    assert "/mode/vs/1" in coordinator._skipped_subdevice_resources

    entry = coordinator._entry
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    registry = dr.async_get(hass)
    stale = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{coordinator.device_key}_1")},
    )
    master = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=coordinator.device_info["identifiers"],
    )
    assert await async_remove_config_entry_device(hass, entry, stale) is True
    assert await async_remove_config_entry_device(hass, entry, master) is False


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("DeviceType_NULL", "DeviceType_TEST-HOOD"),
        ("BTState_Idle", "BTState_Connected"),
        ("Bluetooth_Off", "Bluetooth_On"),
        ("FanSpeed_0", "FanSpeed_1"),
        ("Lamp_Off", "Lamp_On"),
        ("ShutOffTimerSet_0", "ShutOffTimerSet_1"),
        ("ShutOffTimerCurrent_0", "ShutOffTimerCurrent_1"),
        ("ShutOffTimerState_Ready", "ShutOffTimerState_Run"),
        ("DeviceType_NULL", None),
    ],
)
async def test_changed_or_incomplete_hood_status_is_not_filtered(hass, old, new):
    """Synthetic variants limit the exclusion; they do not verify paired hood support."""
    hood = _hood_resources()
    options = hood["/mode/vs/0"]["x.com.samsung.da.options"]
    options.remove(old)
    if new is not None:
        options.append(new)
    coordinator = _coordinator(hass)
    await _discover_cooktop(coordinator, hood)
    assert [s.key for s in coordinator.subdevices] == ["1"]


@pytest.mark.parametrize(
    ("href", "field", "value"),
    [
        ("/information/vs/0", "x.com.samsung.da.modelNum", "OTHER_BOARD"),
        ("/information/vs/0", "x.com.samsung.da.description", "TP6X_CT_16K"),
        ("/information/vs/0", "x.com.samsung.da.description", None),
        ("/power/vs/0", "x.com.samsung.da.power", "On"),
    ],
)
async def test_other_identity_or_active_power_is_not_filtered(hass, href, field, value):
    hood = _hood_resources()
    hood[href][field] = value
    coordinator = _coordinator(hass)
    await _discover_cooktop(coordinator, hood)
    assert [s.key for s in coordinator.subdevices] == ["1"]
