"""Reservoir position from paired native switch tokens."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from homeassistant.helpers.translation import async_get_translations

from custom_components.localthings.binary_sensor import LocalThingsBinarySensor
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities import oven as oven_caps
from custom_components.localthings.registry.entities import BinarySensorDesc
from custom_components.localthings.registry.subdevices import MAIN
from tests.conftest import _load_device
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- installs the fake legacy transport
    _FakeConnection,
    _requests,
    _routes,
)


@pytest.mark.parametrize("divided", [False, True])
async def test_reservoir_uses_native_options_and_tracks_position(hass, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    bound = [
        b
        for b in coordinator.bound
        if b.desc.key == "reservoir_open"
        and b.href.startswith("/mode/")
        and _is_included(b, coordinator)
    ]
    assert len(bound) == 1
    assert bound[0].subdevice == MAIN
    assert bound[0].href == "/mode/vs/0"
    entity = LocalThingsBinarySensor(coordinator, bound[0])
    assert entity.device_class == "opening"
    assert entity.translation_key == "reservoir_open"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "oven_lcd_ov_wall_16k_reservoir.json").read_text()
    )
    _FakeConnection.log.clear()
    for sample in fixture["samples"]:
        coordinator._observe.apply(
            "/mode/vs/0", {PREFIX + "options": sample["options"]}, source="poll"
        )
        coordinator._observe.apply(
            "/doors/vs/0",
            {PREFIX + "items": [{PREFIX + "id": "0", PREFIX + "openState": sample["door"]}]},
            source="poll",
        )
        raw = deepcopy(coordinator.last_resources)
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        assert entity.is_on is (sample["position"] == "open")
        assert coordinator.data["door_open"] is True
        assert PREFIX + "options" not in coordinator.resource("/mode/vs/1")
        assert coordinator.last_resources == raw
        assert coordinator.resource("/mode/vs/0")[PREFIX + "options"] == sample["options"]
    for options in (
        [],
        None,
        ["waterTankInSwitch_On"],
        ["waterTankInSwitch_On", "waterTankOutSwitch_On"],
    ):
        coordinator._observe.apply("/mode/vs/0", {PREFIX + "options": options}, source="poll")
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        assert entity.is_on is None
    assert not _requests()


@pytest.mark.parametrize(
    "model", [None, 16, "LCD_OV_WALL", "LCD_OV_WALL_16K_OTHER", "LCD_OV_WALL_20K"]
)
def test_reservoir_uses_native_switches_regardless_of_board(model):
    resources = {"/information/vs/0": {PREFIX + "modelNum": model}}
    rep = {PREFIX + "options": ["waterTankInSwitch_On", "waterTankOutSwitch_Off"]}
    assert oven_caps._has_reservoir(rep, resources)


def test_reservoir_reads_the_existing_tp2x_steam_oven_fixture():
    from custom_components.localthings.registry.by_type import oven
    from custom_components.localthings.registry.discovery import discover

    resources = _load_device("oven_tp2x_ks_walloven")
    bound = discover(resources, oven.REGISTRY.capabilities)
    assert flatten(bound, resources)["reservoir_open"] is False


def test_combo_reservoir_belongs_to_the_oven_cavity():
    from custom_components.localthings.registry.by_type import resolve
    from custom_components.localthings.registry.discovery import discover
    from tests.test_legacy_http_nw9300md import _canonical, _seed, _sweep

    transport = _sweep(divided=True)
    for index in (0, 2, 3):
        resources = _canonical(_seed(transport, index), index)
        registry = resolve(resources)
        assert registry is not None
        state = flatten(discover(resources, registry.capabilities), resources)
        assert ("reservoir_open" in state) is (index == 2)
        if index == 2:
            assert state["reservoir_open"] is False


@pytest.mark.parametrize(
    "options", [None, [], "invalid", 1, ["Sound_On"], ["waterTankInSwitch_On"]]
)
def test_reservoir_is_not_created_without_both_native_switch_tokens(options):
    resources = {"/information/vs/0": {PREFIX + "modelNum": "LCD_OV_WALL_16K|1|2"}}
    assert not oven_caps._has_reservoir({PREFIX + "options": options}, resources)


@pytest.mark.parametrize(
    "language,name",
    [
        ("cs", "Nádržka"),
        ("de", "Wassertank"),
        ("en", "Reservoir"),
        ("es", "Depósito de agua"),
        ("fr", "Réservoir"),
        ("it", "Serbatoio"),
        ("ko", "물통"),
        ("nl", "Waterreservoir"),
        ("pl", "Zbiornik wody"),
        ("sk", "Nádržka"),
    ],
)
async def test_reservoir_name_loads_in_every_language(
    hass, enable_custom_integrations, language, name
):
    strings = await async_get_translations(hass, language, "entity", {DOMAIN})
    assert strings[f"component.{DOMAIN}.entity.binary_sensor.reservoir_open.name"] == name


@pytest.mark.parametrize(
    "options,expected",
    [
        (["waterTankInSwitch_On", "waterTankOutSwitch_Off"], False),
        (["waterTankInSwitch_Off", "waterTankOutSwitch_On", "Sound_On"], True),
        (["waterTankInSwitch_On", "waterTankOutSwitch_On"], None),
        (["waterTankInSwitch_Off", "waterTankOutSwitch_Off"], None),
        (["waterTankInSwitch_On"], None),
        (["waterTankOutSwitch_On"], None),
        (["waterTankInSwitch_Unknown", "waterTankOutSwitch_On"], None),
        (["waterTankInSwitch_On", "waterTankInSwitch_Off", "waterTankOutSwitch_On"], None),
        (["waterTankInSwitch_On", "waterTankOutSwitch_On", "waterTankOutSwitch_Off"], None),
        ([None, 1, {}], None),
        ([], None),
        (None, None),
        ("waterTankInSwitch_On", None),
        ({"waterTankInSwitch_On": True}, None),
        (42, None),
    ],
)
def test_reservoir_reports_only_confirmed_position_pairs(options, expected):
    desc = next(e for e in oven_caps.OVEN_MODE.entities if e.key == "reservoir_open")
    assert isinstance(desc, BinarySensorDesc)
    assert desc.value_fn(options) is expected
