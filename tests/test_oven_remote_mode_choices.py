"""Specless oven choices follow start validation while preserving current-mode display."""

import json
from copy import deepcopy

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.registry.by_type import oven as oven_registry
from custom_components.localthings.registry.capabilities import cook, oven
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

EXCLUDED = (
    "Drain",
    "Autocook",
    "SelfClean",
    "PyroFree",
    "Descale",
    "Broil",
    "UpperBroil",
    "BreadProof",
    "Autocook1",
    "LowerBroil",
    "SteamClean",
)


def _mode(coordinator, subdevice=MAIN):
    return next(
        b for b in coordinator.bound if b.subdevice == subdevice and b.desc.key == "oven_mode"
    )


def _resources():
    return {
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
        assert bound.desc is next(d for d in oven.OVEN_MODE.entities if d.key == "oven_mode")
        assert entity._raw_options() == [
            "NoOperation",
            *cook.startable_modes(coordinator.canonical_resources(subdevice)),
        ]
        if subdevice == MAIN:
            assert ("UpperBroil" if divided else "Broil") in live
            assert ("UpperConvectionBake" if divided else "Bake") in entity._raw_options()
        else:
            assert "LowerBake" in entity._raw_options()
            assert all(m == "NoOperation" or m.startswith("Lower") for m in entity._raw_options())
    assert coordinator.last_resources == before
    assert not any(method == "PUT" for method, _ in _requests())


@pytest.mark.parametrize("mode", EXCLUDED)
async def test_excluded_modes_cannot_be_held_or_sent_while_running(hass, mode):
    coordinator = await _discovered(hass, _routes(divided=False))
    bound = _mode(coordinator)
    with pytest.raises(ServiceValidationError) as exc:
        await coordinator.async_send_command(bound, mode)
    assert exc.value.translation_key == "cook_mode_not_startable"
    assert exc.value.translation_placeholders is not None
    assert exc.value.translation_placeholders["mode"] == mode
    assert not any(held.values for held in coordinator._held_cooks.values())

    coordinator._observe.apply("/operational/state/vs/0", {PREFIX + "state": "Run"}, source="poll")
    await coordinator.async_send_command(bound, mode)
    assert not any(method == "PUT" for method, _ in _requests())


@pytest.mark.parametrize("mode", EXCLUDED)
async def test_panel_started_program_keeps_its_display_and_running_status(hass, mode):
    upper = deepcopy(UPPER)
    upper["Mode"]["modes"] = [mode]
    upper["Operation"]["state"] = "Run"
    upper["Oven"]["state"] = "Cooking"
    coordinator = await _discovered(hass, _routes(divided=False, upper=upper))
    coordinator._publish_data()
    entity = LocalThingsSelect(coordinator, _mode(coordinator))

    assert entity._raw_options().count(mode) == 1
    assert entity.current_option == entity._display_option(mode)
    assert entity.state == entity._display_option(mode)
    assert coordinator.data["oven_mode"] == mode
    assert coordinator.data["machine_state"] == "active"
    assert coordinator.data["cycle_active"] is True
    assert coordinator.data["oven_state"] == "cooking"
    assert coordinator.last_resources["/mode/vs/0"][PREFIX + "modes"] == [mode]
    await coordinator.async_send_command(_mode(coordinator), mode)
    assert not any(method == "PUT" for method, _ in _requests())

    coordinator._observe.apply("/mode/vs/0", {PREFIX + "modes": ["NoOperation"]}, source="poll")
    coordinator._observe.apply(
        "/operational/state/vs/0", {PREFIX + "state": "Ready"}, source="poll"
    )
    coordinator._publish_data()
    assert mode not in entity._raw_options()
    assert entity.state == entity._display_option("NoOperation")


@pytest.mark.parametrize("live", [None, [], ["Bake"]])
@pytest.mark.parametrize("mode", ["Broil", "SelfClean", "PanelOnlyProgram"])
def test_current_excluded_program_displays_even_if_missing_from_supported_modes(live, mode):
    resources = _resources()
    rep = resources["/mode/vs/0"]
    rep[PREFIX + "supportedModes"] = live
    rep[PREFIX + "modes"] = [mode]
    desc = _description(resources)
    assert callable(desc.options)
    assert desc.options(resources).count(mode) == 1
    assert desc.write_fn is not None
    assert desc.write_fn(mode, rep, None, resources) is None


async def test_existing_selector_follows_updated_cavity_modes(hass):
    coordinator = await _discovered(hass, _routes(divided=False))
    entity = LocalThingsSelect(coordinator, _mode(coordinator))
    assert "Bake" in entity._raw_options()
    rep = deepcopy(coordinator.last_resources["/mode/vs/0"])
    rep[PREFIX + "supportedModes"] = ["NoOperation", "UpperBroil", "UpperConvectionBake"]
    coordinator._observe.apply("/mode/vs/0", rep, source="poll")
    assert entity._raw_options() == ["NoOperation", "UpperConvectionBake"]


@pytest.mark.parametrize("live", [None, []])
def test_without_reported_modes_no_static_cooking_choices_are_offered(live):
    resources = _resources()
    resources["/mode/vs/0"][PREFIX + "supportedModes"] = live
    desc = _description(resources)
    assert callable(desc.options)
    assert desc.options(resources) == ["NoOperation"]
    assert desc.write_fn is not None
    assert desc.write_fn("Broil", resources["/mode/vs/0"], None, resources) is None
    assert desc.write_fn("Bake", resources["/mode/vs/0"], None, resources) is None


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


@pytest.mark.parametrize("control", ["Start&Setting", "Setting"])
def test_a_reported_mode_spec_keeps_its_existing_setting_choices(control):
    resources = _resources()
    rep = resources["/mode/vs/0"]
    rep[cook.MODE_SPEC] = json.dumps(
        [
            {"mode": "Bake", "control": "Start&Setting"},
            {"mode": "Broil", "control": control},
            {"mode": "Autocook", "control": control},
        ]
    )
    rep[PREFIX + "supportedModes"] = ["NoOperation", "Bake", "Broil", "Autocook"]
    desc = _description(resources)
    assert callable(desc.options)
    assert desc.options(resources) == rep[PREFIX + "supportedModes"]
    assert desc.write_fn is not None
    assert desc.write_fn("Broil", rep, None, resources) is not None
    assert desc.write_fn("Autocook", rep, None, resources) is not None
    assert ("Broil" in cook.startable_modes(resources)) is (control == "Start&Setting")


@pytest.mark.parametrize("divided", [False, True])
def test_legacy_combo_oven_cavities_follow_the_same_start_policy(divided):
    from tests.test_legacy_http_nw9300md import _canonical, _seed, _sweep

    transport = _sweep(divided=divided)
    for index in (2, 3):
        resources = _canonical(_seed(transport, index), index)
        rep = resources["/mode/vs/0"]
        desc = _description(resources)
        live = rep[PREFIX + "supportedModes"]
        assert callable(desc.options)
        assert desc.options(resources) == ["NoOperation", *cook.startable_modes(resources)]
        assert desc.write_fn is not None
        for mode in live:
            if mode != "NoOperation":
                assert (desc.write_fn(mode, rep, None, resources) is not None) is (
                    mode in cook.startable_modes(resources)
                )


async def test_removed_divider_does_not_restore_fallback_lower_modes(hass):
    from tests.test_legacy_http_lower_cavity import _seed, _transport
    from tests.test_legacy_http_transport import _FakeConnection

    coordinator = await _discovered(hass, _routes(divided=True))
    lower = next(s for s in coordinator.subdevices if s.key == "1")
    entity = LocalThingsSelect(coordinator, _mode(coordinator, lower))
    assert "LowerBake" in entity._raw_options()
    _FakeConnection.routes = _routes(divided=False)
    for href, rep in _seed(_transport(), 1).items():
        coordinator._observe.apply(href, rep, source="poll")
    assert entity._raw_options() == ["NoOperation"]


def test_mode_filter_does_not_absorb_other_configuration_changes():
    bound = discover(_resources(), oven_registry.REGISTRY.capabilities)
    for entity in bound:
        if entity.href == "/mode/vs/0" and entity.desc.key != "oven_mode":
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
        "range_ne63a6111ss",
        "oven",
    ],
)
async def test_fixtures_filter_only_specless_oven_cavities(hass, name):
    from tests.test_subdevice_discovery import _coordinator, _discover

    coordinator = _coordinator(hass)
    await _discover(coordinator, name)
    modes = [b for b in coordinator.bound if b.desc.key == "oven_mode"]
    assert modes
    for bound in modes:
        assert bound.desc is next(d for d in oven.OVEN_MODE.entities if d.key == "oven_mode")
        entity = LocalThingsSelect(coordinator, bound)
        resources = coordinator.canonical_resources(bound.subdevice)
        rep = resources["/mode/vs/0"]
        if rep.get(cook.MODE_SPEC) is None:
            expected = ["NoOperation", *cook.startable_modes(resources)]
            current = cook.current_mode(resources)
            if current and current not in expected:
                expected.append(current)
        else:
            expected = cook.selectable_modes(rep[PREFIX + "supportedModes"])
            if "NoOperation" not in expected:
                expected.insert(0, "NoOperation")
        assert entity._raw_options() == expected
