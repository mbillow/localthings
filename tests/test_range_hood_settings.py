"""Range-hood PM metadata and locally verified ventilation settings."""

import datetime
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities import range_hood
from custom_components.localthings.registry.discovery import BoundEntity, discover
from custom_components.localthings.registry.entities import NumberDesc, SensorDesc, TimeDesc
from custom_components.localthings.sensor import LocalThingsSensor
from custom_components.localthings.time import LocalThingsTime
from tests.conftest import _load_device

HREF = "/airlevelcheck/vs/0"


def _desc(key) -> Any:
    return next(e for e in range_hood.AIR_LEVEL_CHECK.entities if e.key == key)


@pytest.mark.parametrize(
    "key,device_class,want",
    [("dust", "pm10", 18), ("fine_dust", "pm25", 14), ("super_fine_dust", "pm1", 9)],
)
def test_pm_entities_publish_numeric_concentrations_with_ha_metadata(key, device_class, want):
    resources = _load_device("range_hood")
    bound = discover(resources, {"/sensors/vs/0": [range_hood.AIR_QUALITY]})
    coordinator = cast(
        LocalThingsCoordinator, SimpleNamespace(device_key="test", data=flatten(bound, resources))
    )
    sensor = LocalThingsSensor(coordinator, next(b for b in bound if b.desc.key == key))
    assert sensor.device_class == SensorDeviceClass(device_class)
    assert sensor.native_unit_of_measurement == "μg/m³"
    assert sensor.native_unit_of_measurement in DEVICE_CLASS_UNITS[SensorDeviceClass(device_class)]
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_value == want
    assert isinstance(sensor.native_value, int)


@pytest.mark.parametrize("values", [[], None, [None], ["bad"]])
def test_invalid_pm_reading_stays_unknown(values):
    rep = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.type": "Dust", "x.com.samsung.da.value": values}
        ]
    }
    bound = discover({"/sensors/vs/0": rep}, {"/sensors/vs/0": [range_hood.AIR_QUALITY]})
    assert flatten(bound, {"/sensors/vs/0": rep})["dust"] is None


def test_smart_ventilate_writes_do_not_overwrite_mode():
    desc = _desc("periodic_air_sensing")
    assert desc.write_fn is not None
    assert desc.write_fn("Off", {"x.com.samsung.da.autoExeState": "Alarm"}) == (
        ["airlevelcheck", "vs", "0"],
        {"x.com.samsung.da.periodicSensingActivationState": "Off"},
    )
    assert desc.write_fn("On", {}) == (
        ["airlevelcheck", "vs", "0"],
        {"x.com.samsung.da.periodicSensingActivationState": "On"},
    )


def test_modes_include_detection_only_and_future_advertised_values():
    desc = _desc("automatic_ventilation_state")
    rep = {
        "x.com.samsung.da.autoExeState": "Airpurify",
        "x.com.samsung.da.supportedAutoExeState": ["Airpurify", "Alarm", "Future"],
    }
    resources = {
        HREF: rep,
        "/information/vs/0": {"x.com.samsung.da.modelNum": "AHD-WW-TP1-22-COMMON|firmware"},
    }
    assert set(desc.options(resources)) == {"Sensing", "Airpurify", "Alarm", "Future"}
    for value in ("Sensing", "Airpurify", "Alarm", "Future"):
        assert desc.write_fn(value, rep, HREF, resources) == (
            ["airlevelcheck", "vs", "0"],
            {"x.com.samsung.da.autoExeState": value},
        )
    assert desc.write_fn("NotSupported", rep, HREF, resources) is None


def test_do_not_disturb_switch_uses_skip_status_only():
    desc = _desc("hood_do_not_disturb")
    assert desc.write_fn("On", {}) == (
        ["airlevelcheck", "vs", "0"],
        {"x.com.samsung.da.periodicSensingSkipStatus": "On"},
    )
    assert desc.write_fn("Off", {}) == (
        ["airlevelcheck", "vs", "0"],
        {"x.com.samsung.da.periodicSensingSkipStatus": "Off"},
    )


@pytest.mark.parametrize(
    "part,local,want",
    [("start", datetime.time(23, 30), "07301300"), ("end", datetime.time(6, 15), "06001415")],
)
def test_local_dnd_time_write_converts_to_utc_and_preserves_partner(part, local, want):
    desc = _desc(f"hood_do_not_disturb_{part}")
    assert isinstance(desc, TimeDesc)
    now = datetime.datetime(2026, 11, 2, 21, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert desc.payload_fn is not None
    assert desc.write_fn is not None
    payload = desc.payload_fn(local, now)
    assert desc.write_fn(payload, {"x.com.samsung.da.periodicSensingSkipTime": "06001300"}) == (
        ["airlevelcheck", "vs", "0"],
        {"x.com.samsung.da.periodicSensingSkipTime": want},
    )


@pytest.mark.parametrize(
    "date,start,end",
    [
        ("2026-11-03T04:00:00Z", datetime.time(22), datetime.time(5)),
        ("2026-07-03T03:00:00Z", datetime.time(23), datetime.time(6)),
    ],
)
def test_dnd_utc_clock_is_presented_in_local_timezone(monkeypatch, date, start, end):
    from homeassistant.util import dt as dt_util

    with freeze_time(date), monkeypatch.context() as patch:
        patch.setattr(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("America/Los_Angeles"))
        for part, want in (("start", start), ("end", end)):
            desc = _desc(f"hood_do_not_disturb_{part}")
            raw = desc.value_fn("06001300")
            bound = BoundEntity(href=HREF, capability=range_hood.AIR_LEVEL_CHECK, desc=desc)
            coordinator = cast(
                LocalThingsCoordinator, SimpleNamespace(device_key="test", data={desc.key: raw})
            )
            assert LocalThingsTime(coordinator, bound).native_value == want


@pytest.mark.parametrize(
    "raw", [None, "", "00000000", "24601300", "06009999", "060013", "abcdef00"]
)
def test_malformed_dnd_window_does_not_produce_a_clock(raw):
    for part in ("start", "end"):
        assert _desc(f"hood_do_not_disturb_{part}").value_fn(raw) is None


def test_absent_setting_fields_do_not_create_phantom_controls():
    rep = {"x.com.samsung.da.sensingState": "NonProcessing"}
    bound = discover({HREF: rep}, {HREF: [range_hood.AIR_LEVEL_CHECK]})
    coordinator = cast(
        LocalThingsCoordinator,
        SimpleNamespace(
            discovery_resources={HREF: rep}, discovery_canonical=lambda subdevice: {HREF: rep}
        ),
    )
    keys = {b.desc.key for b in bound if _is_included(b, coordinator)}
    assert not keys.intersection(
        {
            "periodic_air_sensing",
            "automatic_ventilation_state",
            "hood_do_not_disturb",
            "hood_do_not_disturb_start",
            "hood_do_not_disturb_end",
            "air_sensing_interval",
        }
    )


def test_interval_is_read_only_duration_and_superseded_sensors_are_removed():
    assert not any(isinstance(e, NumberDesc) for e in range_hood.AIR_LEVEL_CHECK.entities)
    desc = _desc("air_sensing_interval")
    assert desc.device_class == "duration"
    assert desc.unit == "s"
    assert desc.value_fn("3600") == 3600
    keys = {e.key for e in range_hood.AIR_LEVEL_CHECK.entities}
    assert {"periodic_air_sensing", "automatic_ventilation_state"} <= keys


@pytest.mark.parametrize(
    "grade,want",
    [(1, "good"), (2, "normal"), (3, "poor"), (4, "very_poor"), (9, "9"), (None, None)],
)
def test_pm_air_quality_uses_reported_grade_not_recomputed_concentration(grade, want):
    rep = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.type": "Dust", "x.com.samsung.da.value": ["76", grade]}
        ]
    }
    bound = discover({"/sensors/vs/0": rep}, {"/sensors/vs/0": [range_hood.AIR_QUALITY]})
    state = flatten(bound, {"/sensors/vs/0": rep})
    assert state["dust_air_quality"] == want
    desc = next(e for e in range_hood.AIR_QUALITY.entities if e.key == "dust_air_quality")
    assert isinstance(desc, SensorDesc)
    assert desc.device_class == "enum"
    assert desc.unit is None
    assert desc.state_class is None


@pytest.mark.parametrize(
    "sensor_type,key",
    [("Dust", "dust"), ("FineDust", "fine_dust"), ("SuperFineDust", "super_fine_dust")],
)
def test_sensing_grade_10_is_processing_for_each_particle_size(sensor_type, key):
    rep = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.type": sensor_type, "x.com.samsung.da.value": ["25", "10"]}
        ]
    }
    resources = {"/sensors/vs/0": rep}
    bound = discover(resources, {"/sensors/vs/0": [range_hood.AIR_QUALITY]})
    state = flatten(bound, resources)
    assert state[f"{key}_air_quality"] == "processing"
    desc = next(e for e in range_hood.AIR_QUALITY.entities if e.key == f"{key}_air_quality")
    assert isinstance(desc, SensorDesc)
    assert "processing" in desc.options
    assert state[key] == 25


def test_observed_grade_mapping_for_all_three_particle_sizes():
    rep = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.type": t, "x.com.samsung.da.value": v}
            for t, v in [
                ("Dust", ["76", "2"]),
                ("FineDust", ["63", "3"]),
                ("SuperFineDust", ["35", "2"]),
            ]
        ]
    }
    bound = discover({"/sensors/vs/0": rep}, {"/sensors/vs/0": [range_hood.AIR_QUALITY]})
    state = flatten(bound, {"/sensors/vs/0": rep})
    assert state["dust_air_quality"] == "normal"
    assert state["fine_dust_air_quality"] == "poor"
    assert state["super_fine_dust_air_quality"] == "normal"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("Kr1", "good"),
        ("Kr2", "normal"),
        ("Kr3", "poor"),
        ("Kr4", "very_poor"),
        ("Kr9", "Kr9"),
        (None, None),
    ],
)
def test_last_sensing_level_uses_hood_quality_enum(raw, want):
    desc = _desc("last_air_sensing_level")
    assert desc.value_fn(raw) == want
    assert desc.device_class == "enum"
    assert desc.options == ("good", "normal", "poor", "very_poor")
    assert desc.unit is None
    assert desc.state_class is None


@pytest.mark.parametrize(
    "raw,want",
    [("1", "good"), ("2", "normal"), ("3", "poor"), ("4", "very_poor"), ("9", "9"), (None, None)],
)
def test_clean_level_uses_hood_quality_enum(raw, want):
    resources = {
        "/sensors/vs/0": {
            "x.com.samsung.da.items": [
                {"x.com.samsung.da.type": "CleanLevel", "x.com.samsung.da.value": [raw]}
            ]
        }
    }
    bound = discover(resources, {"/sensors/vs/0": [range_hood.AIR_QUALITY]})
    assert flatten(bound, resources)["clean_level"] == want
    desc = next(e for e in range_hood.AIR_QUALITY.entities if e.key == "clean_level")
    assert isinstance(desc, SensorDesc)
    assert desc.device_class == "enum"
    assert desc.options == ("good", "normal", "poor", "very_poor")
    assert desc.unit is None
    assert desc.state_class is None


@pytest.mark.parametrize(
    "key,translation_key",
    [
        ("periodic_air_sensing", "smart_ventilate"),
        ("automatic_ventilation_state", "smart_ventilate_mode"),
    ],
)
def test_hood_keeps_registry_keys_and_uses_distinct_display_names(key, translation_key):
    assert _desc(key).translation_key == translation_key


@pytest.mark.parametrize("key", ["dust", "fine_dust", "super_fine_dust"])
def test_concentration_names_keep_existing_translation_keys(key):
    desc = next(e for e in range_hood.AIR_QUALITY.entities if e.key == key)
    assert desc.translation_key in (None, key)


@pytest.mark.parametrize(
    "now,local,want",
    [
        (
            datetime.datetime(2026, 3, 8, 0, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
            datetime.time(4),
            datetime.time(11),
        ),
        (
            datetime.datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
            datetime.time(3),
            datetime.time(11),
        ),
    ],
)
def test_dnd_write_uses_target_offset_across_dst(now, local, want):
    desc = _desc("hood_do_not_disturb_start")
    payload = desc.payload_fn(local, now)
    _, body = desc.write_fn(payload, {"x.com.samsung.da.periodicSensingSkipTime": "06001300"})
    assert body["x.com.samsung.da.periodicSensingSkipTime"][:4] == want.strftime("%H%M")


def test_purifier_unset_window_remains_wall_clock_midnight():
    from custom_components.localthings.registry.capabilities import air_purifier

    times = [d for d in air_purifier.AIR_LEVEL_CHECK.entities if isinstance(d, TimeDesc)]
    assert len(times) == 2
    assert all(d.value_fn("00000000") == datetime.time(0) for d in times)


@pytest.mark.parametrize(
    "part,picked,want",
    [("start", datetime.time(23), "07001300"), ("end", datetime.time(6), "06001400")],
)
def test_single_dnd_edit_preserves_other_half(part, picked, want):
    desc = _desc(f"hood_do_not_disturb_{part}")
    now = datetime.datetime(2026, 11, 2, 21, tzinfo=ZoneInfo("America/Los_Angeles"))
    _, body = desc.write_fn(
        desc.payload_fn(picked, now), {"x.com.samsung.da.periodicSensingSkipTime": "06001300"}
    )
    assert body == {"x.com.samsung.da.periodicSensingSkipTime": want}


@pytest.mark.parametrize("board", [None, "OTHER-BOARD", "AHD-WW-TP1-22-COMMON|firmware"])
@pytest.mark.parametrize(
    "current,supported,want",
    [
        ("Airpurify", ["Airpurify", "Alarm", "Airpurify", None, ""], ["Airpurify", "Alarm"]),
        ("Off", ["Airpurify"], ["Airpurify", "Off"]),
        ("Sensing", ["Alarm"], ["Alarm", "Sensing"]),
        ("Alarm", ["Sensing", "Alarm"], ["Sensing", "Alarm"]),
        ("Alarm", None, ["Alarm"]),
        ("", "Alarm", []),
        (None, {}, []),
    ],
)
def test_mode_options_and_writes_use_same_evidence(board, current, supported, want):
    desc = _desc("automatic_ventilation_state")
    rep = {
        "x.com.samsung.da.autoExeState": current,
        "x.com.samsung.da.supportedAutoExeState": supported,
    }
    resources = {HREF: rep}
    if board is not None:
        resources["/information/vs/0"] = {"x.com.samsung.da.modelNum": board}
    expected = set(want)
    if board == "AHD-WW-TP1-22-COMMON|firmware":
        expected.add("Sensing")
    options = desc.options(resources)
    assert set(options) == expected
    assert len(options) == len(expected)
    for mode in expected:
        assert desc.write_fn(mode, rep, HREF, resources) == (
            ["airlevelcheck", "vs", "0"],
            {"x.com.samsung.da.autoExeState": mode},
        )
    assert desc.write_fn("Bogus", rep, HREF, resources) is None
    if "Sensing" not in expected:
        assert desc.write_fn("Sensing", rep, HREF, resources) is None
