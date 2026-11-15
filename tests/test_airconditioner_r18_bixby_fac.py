"""R18_BIXBY_FAC_TIME_23K floor-standing AC (#839): speaker volume, the
always-on display and the speaker's Bluetooth pairing."""

import datetime

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import airconditioner
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "oic.d.airconditioner")


def _bound():
    resources = _load_device("airconditioner_r18_bixby_fac_time_23k")
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "airconditioner"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return bound, resources, unbound


def _desc(cap, key):
    return next(e for e in cap.entities if e.key == key)


def _write(cap, key, payload):
    write = _desc(cap, key).write_fn
    assert write is not None
    return write(payload, {}, cap.href)


def test_no_unbound_hrefs():
    _, _, unbound = _bound()
    assert unbound == []


def test_reads_from_the_fixture():
    bound, resources, _ = _bound()
    state = flatten(bound, resources)
    assert state["speaker_volume"] == 12
    assert state["always_on_display"] is False
    assert state["always_on_display_mode"] == "Always"
    assert state["always_on_display_start"] == datetime.time(21, 0)
    assert state["always_on_display_end"] == datetime.time(14, 0)
    assert state["bluetooth_speaker"] is False
    assert state["bluetooth_discoverable"] is False


def test_volume_is_an_open_ended_box():
    desc = _desc(airconditioner.SPEAKER_VOLUME, "speaker_volume")
    assert desc.mode == "box"
    assert desc.native_min == 0 and desc.native_max is None
    assert _write(airconditioner.SPEAKER_VOLUME, "speaker_volume", 7.0) == (
        ["audio", "vs", "0"],
        {"x.com.samsung.da.volumeLevel": 7},
    )


def test_always_on_display_writes():
    cap = airconditioner.ALWAYS_ON_DISPLAY
    path = ["option", "alwaysdisplay", "vs", "0"]
    assert _write(cap, "always_on_display", "On") == (path, {"status": "On"})
    assert _write(cap, "always_on_display_mode", "Scheduled") == (path, {"mode": "Scheduled"})
    assert _write(cap, "always_on_display_start", datetime.time(7, 5)) == (
        path,
        {"startTime": "0705"},
    )
    assert _write(cap, "always_on_display_end", datetime.time(23, 30)) == (
        path,
        {"endTime": "2330"},
    )


@pytest.mark.parametrize("raw", ["", None, "25", "2460", "ab00"])
def test_unreadable_display_times_are_unknown(raw):
    desc = _desc(airconditioner.ALWAYS_ON_DISPLAY, "always_on_display_start")
    assert desc.value_fn(raw) is None


def test_bluetooth_reads_and_discoverable_write():
    cap = airconditioner.BLUETOOTH_SPEAKER
    assert _desc(cap, "bluetooth_speaker").value_fn("Connected") is True
    assert _write(cap, "bluetooth_discoverable", "On") == (
        ["speaker", "btconnection", "vs", "0"],
        {"x.com.samsung.da.discoverableMode": "On"},
    )
