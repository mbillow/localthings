"""Remote mode choices for LCD_OV_WALL_16K without changing reported state."""

from copy import deepcopy

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.registry.by_type import oven as oven_registry
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.select import LocalThingsSelect
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    UPPER,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _requests,
    _routes,
)

EXCLUDED = ("Drain", "Autocook", "SelfClean", "PyroFree", "Descale", "Broil", "UpperBroil")


def _mode(coordinator, subdevice=MAIN):
    return next(
        b for b in coordinator.bound if b.subdevice == subdevice and b.desc.key == "oven_mode"
    )


def _resources(model="LCD_OV_WALL_16K|1|2"):
    return {
        "/information/vs/0": {PREFIX + "modelNum": model},
        "/mode/vs/0": {
            PREFIX + "modes": ["NoOperation"],
            PREFIX + "supportedModes": [
                "ConvectionBake",
                "Broil",
                "Bake",
                "SelfClean",
                "NoOperation",
            ],
        },
    }


def _description(resources):
    bound = discover(resources, oven_registry.REGISTRY.capabilities)
    modes = [b for b in bound if b.desc.key == "oven_mode"]
    assert len(modes) == 1
    return modes[0].desc


@pytest.mark.parametrize("divided", [False, True])
async def test_captured_cavity_choices_keep_order_and_the_raw_snapshot(hass, divided):
    coordinator = await _discovered(hass, _routes(divided=divided))
    before = deepcopy(coordinator.last_resources)
    cavities = (MAIN, *coordinator.subdevices) if divided else (MAIN,)
    for subdevice in cavities:
        bound = _mode(coordinator, subdevice)
        entity = LocalThingsSelect(coordinator, bound)
        live = coordinator.canonical_resources(subdevice)["/mode/vs/0"][PREFIX + "supportedModes"]
        assert bound.capability is oven.LCD_WALL_MODE
        assert entity._raw_options() == [m for m in live if m not in EXCLUDED]
        if subdevice == MAIN:
            assert ("UpperBroil" if divided else "Broil") in live
            assert ("UpperConvectionBake" if divided else "Bake") in entity._raw_options()
        else:
            assert entity._raw_options() == [
                "NoOperation",
                "LowerBake",
                "LowerConvectionBake",
                "LowerConvectionRoast",
            ]
    assert coordinator.last_resources == before
    assert not any(method == "PUT" for method, _ in _requests())


@pytest.mark.parametrize("mode", EXCLUDED)
async def test_excluded_modes_cannot_be_held_or_sent_while_running(hass, mode):
    coordinator = await _discovered(hass, _routes(divided=False))
    bound = _mode(coordinator)
    with pytest.raises(ServiceValidationError):
        await coordinator.async_send_command(bound, mode)
    assert not any(held.values for held in coordinator._held_cooks.values())

    coordinator._observe.apply("/operational/state/vs/0", {PREFIX + "state": "Run"}, source="poll")
    await coordinator.async_send_command(bound, mode)
    assert not any(method == "PUT" for method, _ in _requests())


@pytest.mark.parametrize("mode", ["SelfClean", "Broil"])
async def test_panel_started_program_keeps_raw_mode_and_running_status(hass, mode):
    upper = deepcopy(UPPER)
    upper["Mode"]["modes"] = [mode]
    upper["Operation"]["state"] = "Run"
    upper["Oven"]["state"] = "Cooking"
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    coordinator._publish_data()
    entity = LocalThingsSelect(coordinator, _mode(coordinator))

    assert mode not in entity._raw_options()
    assert entity.current_option == entity._display_option(mode)
    # HA requires the select's state to be in its choices; raw status is retained.
    assert entity.state is None
    assert coordinator.data["oven_mode"] == mode
    assert coordinator.data["machine_state"] == "active"
    assert coordinator.data["cycle_active"] is True
    assert coordinator.data["oven_state"] == "cooking"
    assert coordinator.last_resources["/mode/vs/0"][PREFIX + "modes"] == [mode]
    assert mode in coordinator.last_resources["/mode/vs/0"][PREFIX + "supportedModes"]


async def test_existing_selector_follows_updated_cavity_modes(hass):
    coordinator = await _discovered(hass, _routes(divided=False))
    entity = LocalThingsSelect(coordinator, _mode(coordinator))
    assert "Bake" in entity._raw_options()
    rep = deepcopy(coordinator.last_resources["/mode/vs/0"])
    rep[PREFIX + "supportedModes"] = ["NoOperation", "UpperBroil", "UpperConvectionBake"]
    coordinator._observe.apply("/mode/vs/0", rep, source="poll")
    assert entity._raw_options() == ["NoOperation", "UpperConvectionBake"]


@pytest.mark.parametrize("live", [None, []])
def test_fallback_choices_and_writes_also_exclude_broil(live):
    resources = _resources()
    resources["/mode/vs/0"][PREFIX + "supportedModes"] = live
    desc = _description(resources)
    assert callable(desc.options)
    assert desc.options(resources) == [m for m in oven._OVEN_MODES if m != "Broil"]
    assert desc.write_fn is not None
    assert desc.write_fn("Broil", resources["/mode/vs/0"], None, resources) is None
    assert desc.write_fn("Bake", resources["/mode/vs/0"], None, resources) == (
        ["mode", "vs", "0"],
        {PREFIX + "modes": ["Bake"]},
    )


def test_allowed_codes_and_idle_validation_stay_unchanged():
    resources = _resources()
    modes = ["FutureMode", "Healthycook6", "UpperHealthycook1", "LowerBake", "Bake"]
    resources["/mode/vs/0"][PREFIX + "supportedModes"] = [*modes, "HOMECARE_WIZARD_V2"]
    desc = _description(resources)
    assert callable(desc.options)
    assert desc.options(resources) == ["NoOperation", *modes]
    assert desc.write_fn is not None
    for mode in modes:
        assert desc.write_fn(mode, resources["/mode/vs/0"], None, resources) == (
            ["mode", "vs", "0"],
            {PREFIX + "modes": [mode]},
        )
    assert desc.write_fn("UnknownMode", resources["/mode/vs/0"], None, resources) is None
    assert desc.validate_fn is not None
    assert desc.validate_fn("NoOperation", {}, resources) == "oven_mode_idle_not_selectable"


@pytest.mark.parametrize(
    "model", [None, 16, "LCD_OV_WALL", "LCD_OV_WALL_16K_OTHER", "LCD_OV_WALL_20K"]
)
def test_other_model_identities_keep_the_shared_descriptor(model):
    resources = _resources(model)
    desc = _description(resources)
    original = next(d for d in oven.OVEN_MODE.entities if d.key == "oven_mode")
    assert desc is original
    assert callable(desc.options)
    assert desc.options(resources) == resources["/mode/vs/0"][PREFIX + "supportedModes"]
    assert desc.write_fn is not None
    assert desc.write_fn("Broil", resources["/mode/vs/0"], None, resources) is not None


def test_mode_filter_does_not_absorb_other_configuration_changes():
    bound = discover(_resources(), oven_registry.REGISTRY.capabilities)
    for entity in bound:
        if entity.capability is oven.LCD_WALL_MODE and entity.desc.key != "oven_mode":
            assert entity.desc is next(
                d for d in oven.OVEN_MODE.entities if d.key == entity.desc.key
            )


@pytest.mark.parametrize(
    "name",
    [
        "microwave_nw9300md",
        "range_tp1x_da_ks_range_0101x",
        "oven_tp2x_ks_walloven",
        "oven_nv75n_dual_cook",
    ],
)
async def test_other_appliances_keep_their_oven_choices(hass, name):
    from tests.test_subdevice_discovery import _coordinator, _discover

    coordinator = _coordinator(hass)
    await _discover(coordinator, name)
    modes = [b for b in coordinator.bound if b.desc.key == "oven_mode"]
    assert modes
    for bound in modes:
        assert bound.desc is next(d for d in oven.OVEN_MODE.entities if d.key == "oven_mode")
        entity = LocalThingsSelect(coordinator, bound)
        resources = coordinator.canonical_resources(bound.subdevice)
        assert entity._raw_options() == oven._oven_mode_options(resources)
