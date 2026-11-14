"""EHS /ehscycle/vs/0 cycle-log decoding (registry/capabilities/ehs_cycle.py)."""

from datetime import timedelta
from typing import Any

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import ehs_cycle
from custom_components.localthings.registry.capabilities.common import parse_iso_utc
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device, _load_device_full

HREF = "/ehscycle/vs/0"
_MIM_H04EN_HEAT_PUMP = "/0a1b2c3d-0000-4000-8000-000001200000/device/0"

# S905D3_SAC_EHS_24 samples and the same dump's /overview/vs/0 readings, as
# posted in #697.
_S905D3_REP: dict[str, Any] = {
    "unit": "Celsius",
    "indoor": [
        {
            "cycledata": "5805565c0505009f630000000000000000010000000973fd0000006b05054b4b64644f4f",
            "datetime": "2026-11-07T22:06:22",
        }
    ],
    "outdoor": [
        {
            "cycledata": "002900295c6d32323502012e024e0003ffff001e0075786200000000",
            "datetime": "2026-11-07T22:06:22",
        }
    ],
}
_S905D3_OVERVIEW = {
    "compwaterouttemp": 37.0,
    "compwaterintemp": 31.5,
    "lpm": 16.0,
    "outsidetemp": -2.0,
}


def _resources(name):
    if name == "ehs_mim_h04en":
        # The Wi-Fi kit's heat pump is a subdevice; see test_ehs_mim_h04en.
        _, _, seeds = _load_device_full(name)
        return parse_device0_batch(seeds[_MIM_H04EN_HEAT_PUMP])
    return _load_device(name)


def _desc(key):
    return next(e for e in ehs_cycle.EHS_CYCLE.entities if e.key == key)


def _newest_time(rep):
    return parse_iso_utc(rep["indoor"][-1]["datetime"])


def _at(monkeypatch, when):
    monkeypatch.setattr(ehs_cycle, "_now", lambda: when)


def _state(name, monkeypatch, *, age=timedelta(minutes=1)):
    resources = _resources(name)
    _at(monkeypatch, _newest_time(resources[HREF]) + age)
    reg = resolve(resources)
    assert reg is not None
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources), resources


def test_decodes_the_01001_dump(monkeypatch):
    state, _ = _state("ehs", monkeypatch)
    assert state["cycle_flow_temperature"] == 29.0
    assert state["cycle_return_temperature"] == 29.0
    assert state["cycle_flow_rate"] == 0.0
    assert state["cycle_pump_speed"] == 0.0
    assert state["compressor_frequency"] == 0.0
    assert state["compressor_target_frequency"] == 0.0
    assert state["cycle_outdoor_temperature"] == 18.0
    assert state["discharge_temperature"] == 35.0
    assert state["evaporator_saturation_temperature"] == 18.0
    assert state["suction_temperature"] == 27.0
    assert state["cycle_updated"] == parse_iso_utc("2026-08-01T21:28:08")


def test_decodes_the_two_zone_01002_dump(monkeypatch):
    state, _ = _state("ehs_01002", monkeypatch)
    assert state["cycle_flow_temperature"] == 44.0
    assert state["cycle_return_temperature"] == 41.0


@pytest.mark.parametrize("name", ["ehs", "ehs_mim_h04en"])
def test_indoor_byte_27_matches_the_dhw_tank_temperature(name):
    resources = _resources(name)
    newest = bytes.fromhex(resources[HREF]["indoor"][-1]["cycledata"])
    dhw = float(resources["/temperatures/dhw/vs/0"]["x.com.samsung.da.current"])
    assert abs((newest[27] - 55) - dhw) <= 0.5


def test_outdoor_byte_8_matches_the_wifi_kits_outside_temperature(monkeypatch):
    state, resources = _state("ehs_mim_h04en", monkeypatch)
    outside = resources["/temperatures/outside/vs/0"]["x.com.samsung.da.current"]
    newest = bytes.fromhex(resources[HREF]["outdoor"][-1]["cycledata"])
    assert abs((newest[8] - 55) - outside) <= 0.5
    # The kit's own 0.1 °C reading is bound, so the cycle-log copy isn't.
    assert state["outdoor_temperature"] == outside
    assert "cycle_outdoor_temperature" not in state
    # Bytes 6-7 read 0x6D60, a rescaled value rather than a flow rate.
    assert state["cycle_flow_rate"] is None


def test_36_byte_s905d3_record_matches_its_overview(monkeypatch):
    _at(monkeypatch, _newest_time(_S905D3_REP) + timedelta(seconds=30))

    def read(key):
        return _desc(key).rep_fn(_S905D3_REP)

    assert abs(read("cycle_flow_temperature") - _S905D3_OVERVIEW["compwaterouttemp"]) <= 0.5
    assert abs(read("cycle_return_temperature") - _S905D3_OVERVIEW["compwaterintemp"]) <= 0.5
    assert abs(read("cycle_flow_rate") - _S905D3_OVERVIEW["lpm"]) <= 0.5
    assert read("cycle_outdoor_temperature") == _S905D3_OVERVIEW["outsidetemp"]


@pytest.mark.parametrize(
    ("high", "low", "lpm"),
    [
        (0x00, 0x9F, 15.9),
        (0x01, 0x04, 26.0),
        (0x03, 0xE8, 100.0),
        (0x2A, 0xF8, None),
        (0x80, 0x00, None),
    ],
)
def test_flow_rate_spans_bytes_6_and_7(monkeypatch, high, low, lpm):
    record = bytearray.fromhex(_S905D3_REP["indoor"][0]["cycledata"])
    record[6:8] = bytes((high, low))
    rep = {**_S905D3_REP, "indoor": [{**_S905D3_REP["indoor"][0], "cycledata": record.hex()}]}
    _at(monkeypatch, _newest_time(rep))
    assert _desc("cycle_flow_rate").rep_fn(rep) == lpm


def test_the_newest_sample_is_read(monkeypatch):
    resources = _load_device("ehs")
    rep = resources[HREF]
    _at(monkeypatch, _newest_time(rep))
    oldest = bytes.fromhex(rep["outdoor"][0]["cycledata"])
    newest = bytes.fromhex(rep["outdoor"][-1]["cycledata"])
    assert oldest[7] != newest[7]
    assert _desc("suction_temperature").rep_fn(rep) == newest[7] - 55


def test_a_stale_sample_reads_unknown(monkeypatch):
    state, _ = _state("ehs", monkeypatch, age=timedelta(minutes=31))
    for key in ("cycle_flow_temperature", "cycle_flow_rate", "compressor_frequency"):
        assert state[key] is None, key
    # The timestamp itself still shows when the log last advanced.
    assert state["cycle_updated"] == parse_iso_utc("2026-08-01T21:28:08")


def test_a_sample_dated_ahead_of_now_still_reads(monkeypatch):
    state, _ = _state("ehs", monkeypatch, age=timedelta(hours=-9))
    assert state["cycle_flow_temperature"] == 29.0


def test_unrecognized_record_lengths_read_unknown(monkeypatch):
    when = "2026-08-01T21:28:08"
    _at(monkeypatch, parse_iso_utc(when))
    rep = {
        "indoor": [{"cycledata": "00" * 24, "datetime": when}],
        "outdoor": [{"cycledata": "00" * 18, "datetime": when}],
    }
    assert _desc("cycle_flow_temperature").rep_fn(rep) is None
    assert _desc("cycle_outdoor_temperature").rep_fn(rep) is None


@pytest.mark.parametrize(
    "rep",
    [
        {},
        {"indoor": []},
        {"indoor": [None]},
        {"indoor": [{"datetime": "2026-08-01T21:28:08"}]},
        {"indoor": [{"cycledata": "zz", "datetime": "2026-08-01T21:28:08"}]},
        {"indoor": [{"cycledata": "ABC", "datetime": "2026-08-01T21:28:08"}]},
        {"indoor": [{"cycledata": "00" * 31, "datetime": "not a date"}]},
        {"indoor": [{"cycledata": 31, "datetime": "2026-08-01T21:28:08"}]},
    ],
)
def test_malformed_samples_read_unknown(monkeypatch, rep):
    _at(monkeypatch, parse_iso_utc("2026-08-01T21:28:08"))
    assert _desc("cycle_flow_temperature").rep_fn(rep) is None


def test_only_the_primary_readings_are_enabled_by_default():
    enabled = {e.key for e in ehs_cycle.EHS_CYCLE.entities if e.enabled_default}
    assert enabled == {
        "cycle_flow_temperature",
        "cycle_return_temperature",
        "cycle_flow_rate",
        "cycle_pump_speed",
        "compressor_frequency",
        "cycle_outdoor_temperature",
    }
