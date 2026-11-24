"""The food probe's target is a number that writes item 1 of /temperatures/vs/0.

The fixtures are an NV7000BS/EUB with a probe plugged in: one idle, one
mid-cook with a 60 degree target. On that oven a write of item 1 alone was
accepted and still read back after 20 seconds with the read-back off.
"""

import json

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import oven as oven_registry
from custom_components.localthings.registry.capabilities import cook
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.subdevices import MAIN, canonical_view
from tests.conftest import _discover_full, _load_device, _load_device_full
from tests.test_coordinator_cook_start import _posts, bare_coordinator  # noqa: F401

DEVICE_TYPES = ("oic.wk.d", "oic.d.oven")
P = "x.com.samsung.da."


def _desc():
    bound = discover(
        _load_device("oven_nv7000bs_probe_cooking"), oven_registry.REGISTRY.capabilities
    )
    return next(b.desc for b in bound if b.desc.key == "food_probe_target")


def _main(name):
    resources, oic_res, seeds = _load_device_full(name)
    bound, materialized, _skipped, full, _name = _discover_full(
        resources, oic_res, seeds, DEVICE_TYPES
    )
    return bound, full, canonical_view(MAIN, full, materialized)


def _temps(view):
    return view["/temperatures/vs/0"]


def test_the_target_is_a_number_with_the_probes_own_value():
    bound, full, _view = _main("oven_nv7000bs_probe_cooking")
    state = flatten(bound, full)

    assert state["food_probe_target"] == 60
    assert state["food_probe_setpoint"] == 60
    assert state["oven_setpoint"] == 160


def test_the_number_follows_the_probe_being_plugged_in():
    bound, full, view = _main("oven_nv7000bs_probe_cooking")
    assert flatten(bound, full)["food_probe_target"] == 60

    desc = _desc()
    assert desc.exists_fn is not None
    assert desc.exists_fn(_temps(view), view)

    options = [o.replace("connected", "disconnected") for o in view["/mode/vs/0"][P + "options"]]
    unplugged = {**view, "/mode/vs/0": {**view["/mode/vs/0"], P + "options": options}}
    assert not desc.exists_fn(_temps(unplugged), unplugged)


def test_the_write_sends_item_1_alone_with_its_unit():
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    desc = _desc()
    assert desc.write_fn is not None

    result = desc.write_fn(80, _temps(view), None, view)

    assert result is not None
    path, body = result
    assert path == ["temperatures", "vs", "0"]
    assert body == {
        P + "items": [{P + "id": "1", P + "desired": "80", P + "unit": "Celsius"}],
    }


def test_the_cavity_setpoint_is_never_in_the_probe_write():
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    desc = _desc()
    assert desc.write_fn is not None

    result = desc.write_fn(80, _temps(view), None, view)

    assert result is not None
    _path, body = result
    assert [item[P + "id"] for item in body[P + "items"]] == ["1"]
    assert "160" not in json.dumps(body)


@pytest.mark.parametrize("value", [29, 100, 150, 0, -5])
def test_a_target_outside_the_probe_range_is_refused(value):
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    desc = _desc()
    assert desc.write_fn is not None

    assert desc.write_fn(value, _temps(view), None, view) is None


@pytest.mark.parametrize("value", ["hot", None, ""])
def test_a_non_numeric_target_is_refused(value):
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    desc = _desc()
    assert desc.write_fn is not None

    assert desc.write_fn(value, _temps(view), None, view) is None


def test_the_range_is_the_current_modes_own():
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    assert cook.current_mode(view) == "Conventional"
    assert cook.probe_bounds(view) == (30, 99, 1)

    narrowed = json.loads(view["/mode/vs/0"][P + "modeSpec"])
    for entry in narrowed:
        if entry["mode"] == "Conventional":
            entry["probeMinC"], entry["probeMaxC"] = "45", "90"
    narrowed_view = {
        **view,
        "/mode/vs/0": {**view["/mode/vs/0"], P + "modeSpec": json.dumps(narrowed)},
    }
    desc = _desc()
    assert desc.write_fn is not None

    assert cook.probe_bounds(narrowed_view) == (45, 90, 1)
    assert desc.write_fn(40, _temps(narrowed_view), None, narrowed_view) is None
    assert desc.write_fn(90, _temps(narrowed_view), None, narrowed_view) is not None


def test_idle_the_probe_target_is_neither_offered_nor_written():
    """The target is a second write after the cavity's own, so a plugged-in
    probe on an idle oven gets no number and no write."""
    bound, full, view = _main("oven_nv7000bs_probe_idle")
    assert cook.is_idle(view)
    assert cook.probe_bounds(view) == (30, 99, 1)
    assert "food_probe_target" not in flatten(bound, full)
    assert flatten(bound, full)["food_probe_temp"] == 31
    desc = _desc()
    assert desc.exists_fn is not None
    assert desc.write_fn is not None

    assert not desc.exists_fn(_temps(view), view)
    assert desc.write_fn(60, _temps(view), None, view) is None


def test_a_board_that_declares_no_probe_range_gets_no_number():
    """No modeSpec, no range to check a write against: the sensors stay."""
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    stripped = {
        **view,
        "/mode/vs/0": {k: v for k, v in view["/mode/vs/0"].items() if k != P + "modeSpec"},
    }
    desc = _desc()
    assert desc.exists_fn is not None
    assert desc.write_fn is not None

    assert not desc.exists_fn(_temps(stripped), stripped)
    assert desc.write_fn(60, _temps(stripped), None, stripped) is None


def test_a_mode_that_does_not_support_the_probe_declares_no_range():
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")

    assert cook.probe_bounds(view, "LargeGrill") is None
    assert cook.probe_bounds(view, "AirSousvide") is None
    assert cook.probe_bounds(view, "Convection") == (30, 99, 1)


def test_fahrenheit_boards_use_their_declared_fahrenheit_range_and_unit():
    _bound, _full, view = _main("oven_nv7000bs_probe_cooking")
    items = [dict(i) for i in _temps(view)[P + "items"]]
    for item in items:
        item[P + "unit"] = "Fahrenheit"
    spec = json.loads(view["/mode/vs/0"][P + "modeSpec"])
    for entry in spec:
        if entry["mode"] == "Conventional":
            entry["probeMinF"], entry["probeMaxF"], entry["probeIntervalF"] = "90", "200", "1"
    fahrenheit = {
        **view,
        "/temperatures/vs/0": {**_temps(view), P + "items": items},
        "/mode/vs/0": {**view["/mode/vs/0"], P + "modeSpec": json.dumps(spec)},
    }
    desc = _desc()
    assert desc.write_fn is not None

    result = desc.write_fn(140, _temps(fahrenheit), None, fahrenheit)

    assert result is not None
    _path, body = result
    assert body[P + "items"][0][P + "unit"] == "Fahrenheit"
    assert body[P + "items"][0][P + "desired"] == "140"
    assert desc.write_fn(85, _temps(fahrenheit), None, fahrenheit) is None
    assert desc.write_fn(201, _temps(fahrenheit), None, fahrenheit) is None


# ---------------------------------------------------------------------------
# Through the coordinator: what reaches the oven, and what the cache keeps
# ---------------------------------------------------------------------------


def _load_into(coord, name):
    resources, oic_res, seeds = _load_device_full(name)
    bound, _materialized, _skipped, full, _name = _discover_full(
        resources, oic_res, seeds, DEVICE_TYPES
    )
    for href, rep in resources.items():
        coord._observe.apply(href, rep, source="poll")
    coord.bound = [b for b in bound if b.subdevice is MAIN]
    return full


async def test_the_number_writes_one_item_and_keeps_the_cavity_in_the_cache(
    bare_coordinator,  # noqa: F811
):
    _load_into(bare_coordinator, "oven_nv7000bs_probe_cooking")
    probe = next(b for b in bare_coordinator.bound if b.desc.key == "food_probe_target")

    sent = await bare_coordinator.async_send_command(probe, 80)

    assert sent is not False
    path, body = _posts(bare_coordinator)[-1]
    assert path == ["temperatures", "vs", "0"]
    assert body == {P + "items": [{P + "id": "1", P + "desired": "80", P + "unit": "Celsius"}]}
    cached = {
        item[P + "id"]: item
        for item in bare_coordinator.resource("/temperatures/vs/0")[P + "items"]
    }
    assert cached["1"][P + "desired"] == "80"
    assert cached["0"][P + "desired"] == "160"
    assert cached["0"][P + "current"] == "105"


async def test_the_number_refuses_a_write_while_remote_control_is_off(
    bare_coordinator,  # noqa: F811
):
    from homeassistant.exceptions import ServiceValidationError

    _load_into(bare_coordinator, "oven_nv7000bs_probe_cooking")
    off = bare_coordinator.resource("/remotectrl/vs/0")
    bare_coordinator._observe.apply(
        "/remotectrl/vs/0", {**off, P + "remoteControlEnabled": "false"}, source="poll"
    )
    probe = next(b for b in bare_coordinator.bound if b.desc.key == "food_probe_target")

    with pytest.raises(ServiceValidationError):
        await bare_coordinator.async_send_command(probe, 80)
    assert _posts(bare_coordinator) == []
