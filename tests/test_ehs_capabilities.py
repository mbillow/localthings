"""Tests for Samsung EHS (Eco Heating System) heat pump support
(TP1X_DA_AC_EHS_01001_0000).

HA-free like the rest of the suite: exercises the registry, discovery/
flatten, and the zone/dhw mode and temperature write contracts.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc, SelectDesc, WaterHeaterDesc
from tests.conftest import _load_device


def _ehs():
    resources = _load_device("ehs")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound():
    reg, resources = _ehs()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def _desc(key):
    bound, _ = _bound()
    return next(b.desc for b in bound if b.desc.key == key)


def test_model_resolves_to_ehs_registry():
    reg, _ = _ehs()
    assert reg is not None and reg.name == "ehs"


def test_no_unbound_hrefs():
    """Every resource in the real TP1X_DA_AC_EHS_01001_0000 dump binds or is
    covered -- clears the coverage-gap repair a device_type='unknown' entry
    raises."""
    reg, resources = _ehs()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_state_keys_present():
    state = _state()
    for key in (
        "zone_power",
        "zone_mode",
        "zone_temperature",
        "zone_target_temperature",
        "water_heater",
        "away_mode",
        "mute_once",
        "alarm_code",
        "energy_kwh",
    ):
        assert key in state, key


def test_zone_temperature_reads_current_value():
    state = _state()
    assert state["zone_temperature"] == 30.0


def test_zone_target_temperature_reads_desired_value():
    state = _state()
    assert state["zone_target_temperature"] == 5.0


def test_zone_mode_reads_first_mode():
    state = _state()
    assert state["zone_mode"] == "Cool"


def test_zone_mode_select_options_come_from_live_supported_modes():
    """Options are read live from x.com.samsung.da.supportedModes, not a
    hardcoded tuple -- so a future firmware with a different mode set is
    handled automatically."""
    desc = _desc("zone_mode")
    assert isinstance(desc, SelectDesc)
    assert desc.options_field == "x.com.samsung.da.supportedModes"
    assert desc.options == ()


def test_zone_mode_write_contract():
    desc = _desc("zone_mode")
    path, body = desc.write_fn("Heat", {})
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.modes": ["Heat"]}


def test_zone_power_reads_off():
    state = _state()
    assert state["zone_power"] is False


def test_zone_power_write_contract():
    desc = _desc("zone_power")
    path, body = desc.write_fn("On", {})
    assert path == ["power", "vs", "0"]
    assert body == {"x.com.samsung.da.power": "On"}


def test_zone_target_temperature_write_contract():
    desc = _desc("zone_target_temperature")
    assert isinstance(desc, NumberDesc)
    path, body = desc.write_fn("21.5", {})
    assert path == ["temperatures", "indoor", "vs", "0"]
    assert body == {"x.com.samsung.da.desired": "21.5"}


def test_zone_target_temperature_bounds_read_live():
    """min/max/step come from the device's own resource fields rather than
    a hardcoded constant -- see the adding-device-support skill's 'never
    hard-code the one dump's values' section."""
    desc = _desc("zone_target_temperature")
    rep = {"x.com.samsung.da.minimum": "5.0", "x.com.samsung.da.maximum": "25.0"}
    assert desc.native_min_fn(rep) == 5.0
    assert desc.native_max_fn(rep) == 25.0
    assert desc.step_fn({"x.com.samsung.da.increment": "0.5"}) == 0.5
    # No live field: falls back to a sane default rather than raising.
    assert desc.native_min_fn({}) == 5.0
    assert desc.native_max_fn({}) == 30.0
    assert desc.step_fn({}) == 0.5


def test_zone_target_temperature_bounds_fall_back_together():
    """One end without the other is not a usable range: pairing a real
    device minimum with an invented default maximum looks plausible and is
    silently wrong, so a half-reported range falls back whole."""
    desc = _desc("zone_target_temperature")
    half = {"x.com.samsung.da.minimum": "10.0"}
    assert desc.native_min_fn(half) == 5.0
    assert desc.native_max_fn(half) == 30.0


def test_zone_target_temperature_zero_increment_is_not_collapsed():
    """`or` would turn a genuine 0 into the 0.5 default (issue #160)."""
    desc = _desc("zone_target_temperature")
    assert desc.step_fn({"x.com.samsung.da.increment": "0"}) == 0.0


def test_water_heater_entity_is_bound():
    """The composite water_heater entity binds the primary /mode/dhw/vs/0
    resource -- same primary-plus-siblings shape as the AC's ClimateDesc
    (see test_airconditioner_capabilities.py's test_climate_entity_is_bound)."""
    bound, _ = _bound()
    water_heaters = [b for b in bound if isinstance(b.desc, WaterHeaterDesc)]
    assert len(water_heaters) == 1
    assert water_heaters[0].href == "/mode/dhw/vs/0"


def test_water_heater_reads_first_mode():
    """The flattened/golden state exposes the same representative scalar
    the entity's current_operation is derived from -- see climate.py's
    _first_mode for the identical pattern on the AC side."""
    state = _state()
    assert state["water_heater"] == "Eco"


def test_water_heater_write_targets():
    """DHW.entities[0].write_fn maps each (kind, value) command to the right
    vendor POST target and body -- power, mode and temperature only, no fan/
    swing/preset (the AC's climate.py has those; the DHW loop doesn't)."""
    write = _desc("water_heater").write_fn
    assert write(("power", True), {}) == (
        ["power", "dhw", "vs", "0"],
        {"x.com.samsung.da.power": "On"},
    )
    assert write(("power", False), {}) == (
        ["power", "dhw", "vs", "0"],
        {"x.com.samsung.da.power": "Off"},
    )
    assert write(("mode", "Force"), {}) == (
        ["mode", "dhw", "vs", "0"],
        {"x.com.samsung.da.modes": ["Force"]},
    )
    assert write(("temperature", 45.0), {}) == (
        ["temperatures", "dhw", "vs", "0"],
        {"x.com.samsung.da.desired": "45.0"},
    )
    assert write(("bogus", 1), {}) is None


def test_dhw_power_and_temperature_declared_as_coverage():
    """/power/dhw/vs/0 and /temperatures/dhw/vs/0 are read by the composite
    water_heater entity (via water_heater.py's sibling reads), not given
    their own entities -- declared as no-entity coverage caps so discover()
    reports no gap, same pattern as the AC's CLIMATE_CONSUMED_HREFS."""
    reg, _ = _ehs()
    for href in ("/power/dhw/vs/0", "/temperatures/dhw/vs/0"):
        caps = reg.capabilities.get(href)
        assert caps, href
        assert all(c.entities == () for c in caps), href


def test_away_mode_reads_off():
    state = _state()
    assert state["away_mode"] is False


def test_away_mode_write_contract():
    desc = _desc("away_mode")
    path, body = desc.write_fn("On", {})
    assert path == ["option", "outgoing", "vs", "0"]
    assert body == {"x.com.samsung.da.away": "On"}


def test_zone_water_law_offset_reads_zero():
    """The water-law (weather-compensation) offset carried alongside the
    zone setpoint on /temperatures/indoor/vs/0. Samsung's cloud REST API
    doesn't expose this field at all -- reading the device directly does."""
    state = _state()
    assert state["zone_water_law_offset"] == 0.0


def test_zone_water_law_offset_is_diagnostic():
    """Read-only for now -- see ehs.ZONE_TEMPERATURE's comment: reclassify
    to 'config' if/when a write_fn is added."""
    desc = _desc("zone_water_law_offset")
    assert desc.entity_category == "diagnostic"
