"""A lower oven keeps Start registered while its divider or mode list changes."""

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.button import LocalThingsButton
from custom_components.localthings.button import async_setup_entry as setup_buttons
from custom_components.localthings.const import CONF_DISABLED_SUBDEVICES, DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.subdevices import MAIN
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _FakeConnection,
    _lower,
    _routes,
    _seed,
)


@pytest.mark.parametrize("initially_divided", [False, True])
async def test_lower_start_survives_divider_changes_without_reload(
    hass, enable_custom_integrations, initially_divided
):
    coordinator = await _discovered(hass, _routes(divided=initially_divided))
    lower = _lower(coordinator)
    entry = coordinator._entry
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    buttons = []
    await setup_buttons(
        hass, entry, lambda new_entities, update_before_add=False: buttons.extend(new_entities)
    )
    start = next(
        (
            b
            for b in buttons
            if b._bound.subdevice == lower and b._bound.desc.key == "start_cooking"
        ),
        None,
    )
    assert start is not None
    unique_id = start.unique_id

    for divided in (False, True, False, True):
        _FakeConnection.routes = _routes(divided=divided)
        for index in (0, 1):
            resources = _seed(coordinator._session, index)
            for href, rep in resources.items():
                coordinator._observe.apply(href, rep, source="poll")
        coordinator.async_set_updated_data(
            flatten(coordinator.bound, coordinator.entity_resources())
        )
        assert start.unique_id == unique_id
        _FakeConnection.log.clear()
        if not divided:
            with pytest.raises(ServiceValidationError):
                await start.async_press()
            with pytest.raises(ServiceValidationError):
                await coordinator.async_start_cooking(
                    lower, mode="LowerBake", temperature=350, duration=300
                )
            assert not any(method == "PUT" for method, *_ in _FakeConnection.log)

    bound = {b.desc.key: b for b in coordinator.bound if b.subdevice == lower}
    for key, value in (
        ("oven_mode", "LowerBake"),
        ("oven_setpoint", 350),
        ("cook_time", 5),
    ):
        await coordinator.async_send_command(bound[key], value)
    await start.async_press()
    writes = [(path, body) for method, path, body, _ in _FakeConnection.log if method == "PUT"]
    assert writes and all(path == "/devices/1" for path, _ in writes)
    assert writes[0][1] is not None and writes[-1][1] is not None
    assert writes[0][1]["Device"]["Mode"] == {"modes": ["LowerBake"]}
    assert writes[-1][1]["Device"]["Operation"]["state"] == "Run"
    coordinator.last_update_success = False
    assert not start.available

    _FakeConnection.routes = _routes(divided=False)
    for index in (0, 1):
        for href, rep in _seed(coordinator._session, index).items():
            coordinator._observe.apply(href, rep, source="poll")
    coordinator.async_set_updated_data(flatten(coordinator.bound, coordinator.entity_resources()))
    _FakeConnection.log.clear()
    await coordinator.async_start_cooking(MAIN, mode="Bake", temperature=350, duration=300)
    writes = [(path, body) for method, path, body, _ in _FakeConnection.log if method == "PUT"]
    assert writes and all(path == "/devices/0" for path, _ in writes)
    assert writes[0][1] is not None
    assert writes[0][1]["Device"]["Mode"] == {"modes": ["Bake"]}


@pytest.mark.parametrize("spec", [[], [{"mode": "LowerBake", "control": "Setting"}], "malformed"])
async def test_lower_button_keeps_existing_mode_spec_rules(hass, spec):
    coordinator = await _discovered(hass, _routes(divided=False))
    resources = coordinator.canonical_resources(_lower(coordinator))
    resources = {**resources, cook.MODE_HREF: {**resources[cook.MODE_HREF], cook.MODE_SPEC: spec}}
    assert cook.can_start({}, resources) is (spec == [])


@pytest.mark.parametrize("connected", ["On", "Off", "Unknown", None])
@pytest.mark.parametrize("description", ["OTHER_DIV", "Another_DIV", "Unknown"])
def test_empty_lower_modes_do_not_depend_on_divider_status(description, connected):
    resources = {
        "/information/vs/0": {PREFIX + "description": description},
        cook.MODE_HREF: {PREFIX + "supportedModes": ["NoOperation"]},
    }
    if connected is not None:
        resources[cook.CONNECTED_HREF] = {PREFIX + "connected": connected}
    assert cook.can_start({}, resources) is description.endswith("_DIV")
    assert cook.startable_modes(resources) == []
    with pytest.raises(cook.CookStartError):
        cook.plan_start(resources, "LowerBake", 350, 300)


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("connected", ["Off", "Unknown", None])
async def test_snapshot_keeps_start_with_missing_or_unknown_divider(hass, disabled, connected):
    online = await _discovered(hass, _routes(divided=False))
    resources = dict(online.last_resources)
    if connected is None:
        resources.pop("/connected/vs/1", None)
    else:
        resources["/connected/vs/1"] = {PREFIX + "connected": connected}
    await online._async_save_snapshot(resources, online.subdevices)
    if disabled:
        hass.config_entries.async_update_entry(
            online._entry, options={CONF_DISABLED_SUBDEVICES: {"1": "Lower oven"}}
        )
    restored = LocalThingsCoordinator(hass, online._entry)
    assert await restored.async_rehydrate()
    restored.last_update_success = False
    assert restored.last_resources == {}
    starts = [
        LocalThingsButton(restored, b)
        for b in restored.bound
        if b.subdevice.key == "1" and b.desc.key == "start_cooking" and _is_included(b, restored)
    ]
    assert len(starts) == (0 if disabled else 1)
    assert all(not button.available for button in starts)
