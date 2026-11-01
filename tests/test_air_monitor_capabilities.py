"""Tests for the Samsung Air Monitor Plus family (ASM-KR-TP1-22-ACMB1M,
issue #210) -- a standalone, battery-powered air-quality sensor puck. No
controllable appliance state at all beyond the do-not-disturb window.
"""

from datetime import time as dt_time

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_oic_type, resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SensorDesc
from tests.conftest import _load_device


def _air_monitor():
    resources = _load_device("air_monitor")
    reg = resolve(resources)
    return reg, resources


def _bound():
    reg, resources = _air_monitor()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_resolves_to_air_monitor_registry():
    reg, _ = _air_monitor()
    assert reg is not None and reg.name == "air_monitor"


def test_resolves_via_oic_type_too():
    """This board's real dump carries /oic/d's device type
    ('x.com.st.d.airqualitysensor') as well as the 'ASM' modelNum token --
    both paths must agree."""
    reg = for_device_by_oic_type(("x.com.st.d.airqualitysensor",))
    assert reg is not None and reg.name == "air_monitor"


def test_no_unbound_hrefs():
    reg, resources = _air_monitor()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_air_quality_sensors_read_from_shared_items_decode():
    """Same /sensors/vs/0 {type, value} shape and common.sensor_item_value
    decode air_purifier.AIR_QUALITY already uses -- this board lists CO2
    unconditionally, unlike the exists_fn-gated purifier descriptor."""
    state = _state()
    assert state["dust"] == 31
    assert state["fine_dust"] == 23
    assert state["super_fine_dust"] == 18
    assert state["odor"] == 1
    assert state["clean_level"] == 2
    assert state["co2"] == 498


def test_second_value_slot_is_not_exposed():
    """Dust/fine_dust/super_fine_dust each carry a second `value[1]` grade
    code (e.g. Dust's ['31', '2']) with no supported-values field anywhere
    in the dump to explain its scale -- per the 'still never invent... from
    nothing' rule, only the raw reading (value[0]) is bound, not a second
    'dust_level' or similar entity for that code."""
    state = _state()
    assert "dust_level" not in state
    assert "dust_grade" not in state


def test_humidity_and_battery_present():
    state = _state()
    assert state["humidity"] == 57
    assert state["battery"] == 80
    assert state["battery_charging"] is False


def test_air_quality_standard_present():
    state = _state()
    assert state["air_quality_standard"] == "AirCleanAssociation"


def test_dnd_read_contract():
    state = _state()
    assert state["dnd"] is False
    assert state["dnd_start"] == dt_time(14, 0)
    assert state["dnd_end"] == dt_time(22, 0)


def test_dnd_write_contracts_are_flagged_educated_guesses():
    """Issue #210: no idle-vs-active dump pair exists for /dnd/vs/0 (DND
    was never toggled in the one dump this board has), so these write
    contracts are educated guesses -- symmetric with the read side's own
    string/time-format shape, not invented from nothing, but still
    unconfirmed on real hardware. Only the wiring is tested here; whether
    the device actually accepts these writes needs a reporter to confirm."""
    dnd = _desc("dnd")
    path, body = dnd.write_fn("On", {})
    assert path == ["dnd", "vs", "0"]
    assert body == {"x.com.samsung.da.value": "true"}
    path, body = dnd.write_fn("Off", {})
    assert body == {"x.com.samsung.da.value": "false"}

    start = _desc("dnd_start")
    path, body = start.write_fn(dt_time(9, 30), {})
    assert path == ["dnd", "vs", "0"]
    assert body == {"x.com.samsung.da.startTime": "09:30:00"}


def test_a_unit_reporting_temperature_binds_it_in_celsius():
    """Issue #683: some units add the Tizen Lite AC's /temperatures/vs/0."""
    resources = _load_device("air_monitor_temperature")
    reg = resolve(resources)
    assert reg is not None
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    temperature = next(b.desc for b in bound if b.desc.key == "current_temperature_c")

    assert unbound == []
    assert flatten(bound, resources)["current_temperature_c"] == 27.0
    assert isinstance(temperature, SensorDesc) and temperature.unit_fn is not None
    assert temperature.unit_fn(resources["/temperatures/vs/0"]) == "°C"
