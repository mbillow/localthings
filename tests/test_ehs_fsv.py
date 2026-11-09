"""EHS /ehsfsv/vs/0 field setting values (registry/capabilities/ehs_fsv.py)
and the zone water-law offset."""

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import ehs_fsv
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device, _load_device_full

HREF = "/ehsfsv/vs/0"
_MIM_H04EN_HEAT_PUMP = "/0a1b2c3d-0000-4000-8000-000001200000/device/0"

# code -> (minimum, maximum, value) for all 20 records on the 01001 dump.
_EXPECTED_01001 = {
    1031: (37.0, 75.0, 55.0),
    1032: (15.0, 37.0, 15.0),
    1051: (50.0, 70.0, 62.0),
    1052: (30.0, 40.0, 40.0),
    2011: (-20.0, 5.0, -3.0),
    2012: (10.0, 20.0, 15.0),
    2021: (17.0, 75.0, 34.0),
    2022: (17.0, 75.0, 33.0),
    2031: (17.0, 75.0, 65.0),
    2032: (17.0, 75.0, 17.0),
    2091: (0.0, 4.0, 0.0),
    2092: (0.0, 4.0, 0.0),
    2093: (1.0, 4.0, 4.0),
    3011: (0.0, 2.0, 2.0),
    3071: (0.0, 1.0, 0.0),
    4011: (0.0, 1.0, 0.0),
    4012: (-15.0, 20.0, 0.0),
    4021: (0.0, 2.0, 0.0),
    4042: (5.0, 15.0, 10.0),
    4061: (0.0, 1.0, 0.0),
}

_ENUM_KEYS = (
    "fsv_dhw_application_mode",
    "fsv_heating_dhw_priority",
    "fsv_backup_heater_application",
    "fsv_zone_control_application",
)


def _resources(name):
    if name == "ehs_mim_h04en":
        # The Wi-Fi kit's heat pump is a subdevice; see test_ehs_mim_h04en.
        _, _, seeds = _load_device_full(name)
        return parse_device0_batch(seeds[_MIM_H04EN_HEAT_PUMP])
    return _load_device(name)


def _state(name):
    resources = _resources(name)
    reg = resolve(resources)
    assert reg is not None
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


def _desc(key):
    return next(e for e in ehs_fsv.FSV_SENSORS if e.key == key)


def test_every_01001_record_decodes():
    decoded = {}
    for item in _resources("ehs")[HREF]["items"]:
        record = ehs_fsv._decode_setting(item["setting"])
        assert record is not None
        decoded[record.code] = (record.minimum, record.maximum, record.value)
    assert decoded == _EXPECTED_01001


def test_values_are_signed():
    # FSV 4012's minimum is 0xFF6A, which only reads -15.0 as a signed int16.
    record = ehs_fsv._find_fsv(_resources("ehs")[HREF], 4012)
    assert record is not None
    assert (record.minimum, record.maximum) == (-15.0, 20.0)


@pytest.mark.parametrize("name", ["ehs", "ehs_mim_h04en"])
def test_dhw_limits_match_the_dhw_resource(name):
    resources = _resources(name)
    dhw = resources["/temperatures/dhw/vs/0"]
    state = _state(name)
    assert state["fsv_dhw_temperature_maximum"] == float(dhw["x.com.samsung.da.maximum"])
    assert state["fsv_dhw_temperature_minimum"] == float(dhw["x.com.samsung.da.minimum"])


def test_zone_control_reads_two_zones_only_on_the_01002_board():
    resources = _resources("ehs_01002")
    assert "/power/zone2/vs/0" in resources
    assert _state("ehs_01002")["fsv_zone_control_application"] == 1.0
    assert _state("ehs")["fsv_zone_control_application"] == 0.0


def test_a_code_the_unit_does_not_report_creates_no_sensor():
    rep = {"items": [{"setting": item["setting"]} for item in _resources("ehs")[HREF]["items"]]}
    rep["items"] = [i for i in rep["items"] if not i["setting"].startswith("07DB")]  # 2011
    assert _desc("fsv_outdoor_curve_cold_point").exists_fn(rep, {}) is False
    assert _desc("fsv_outdoor_curve_warm_point").exists_fn(rep, {}) is True


@pytest.mark.parametrize(
    "setting",
    [None, 123, "not-hex-zz", "0407010A0101720", "0407010A0101"],
)
def test_malformed_settings_decode_to_none(setting):
    assert ehs_fsv._decode_setting(setting) is None


@pytest.mark.parametrize(
    "rep",
    [None, {}, {"items": "not-a-list"}, {"items": [None]}, {"items": [{"setting": "zz"}]}],
)
def test_malformed_reps_read_unknown(rep):
    assert _desc("fsv_heating_outlet_temperature_maximum").rep_fn(rep) is None


def test_enum_codes_carry_no_unit():
    for key in _ENUM_KEYS:
        assert _desc(key).device_class is None, key
        assert _desc(key).unit is None, key


def test_every_fsv_sensor_is_a_disabled_diagnostic():
    for desc in ehs_fsv.FSV_SENSORS:
        assert desc.enabled_default is False, desc.key
        assert desc.entity_category == "diagnostic", desc.key


@pytest.mark.parametrize(("name", "offset"), [("ehs", 0.0), ("ehs_01002", -1.0)])
def test_zone_water_law_offset(name, offset):
    assert _state(name)["zone_water_law_offset"] == offset
