"""Tests for /airlevelcheck/vs/0 -- the "AI Purify" periodic air-quality
sensing engine (issues #84 and #190).

The resource is reported by three of this registry's four board families, so
the read assertions run against each family's own fixture; the write contracts
were exercised on AVT-WW-TP1-23-AXX500 hardware and are asserted here through
the descriptors that carry them.
"""

import datetime

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import air_purifier
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

# The three fixtures whose dumps carry this resource. air_purifier (the
# ARTIK051_TVTL family, issue #56) has no such href and is deliberately absent.
FAMILIES = ("air_purifier_avt_ww", "air_purifier_vtww", "air_purifier_tp1x_da_ac_air")

HREF = ["airlevelcheck", "vs", "0"]


def _rep(fixture):
    return _load_device(fixture)["/airlevelcheck/vs/0"]


def _state(fixture):
    resources = _load_device(fixture)
    reg = resolve(resources)
    assert reg is not None and reg.name == "air_purifier", fixture
    return flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)


def _desc(key):
    return next(d for d in air_purifier.AIR_LEVEL_CHECK.entities if d.key == key)


def test_air_level_check_is_bound_not_covered():
    """The href used to sit in COVERAGE as opaque scheduler plumbing. Guard
    against it being covered again, which would silently drop every entity
    below while still reporting zero unbound hrefs."""
    covered = {cap.href for cap in air_purifier.COVERAGE}
    assert "/airlevelcheck/vs/0" not in covered


def test_every_reporting_family_binds_the_cluster():
    for fixture in FAMILIES:
        state = _state(fixture)
        for key in (
            "sensing_mode",
            "periodic_air_sensing",
            "periodic_sensing_skip_status",
            "sensing_skip_start",
            "sensing_skip_end",
            "air_sensing_state",
            "last_air_sensing_time",
            "last_air_sensing_level",
        ):
            assert key in state, f"{fixture}: {key}"


def test_tvtl_family_is_untouched():
    """Issue #56's board has no /airlevelcheck href at all -- nothing this
    change adds may appear on it."""
    state = _state("air_purifier")
    for key in ("sensing_mode", "periodic_air_sensing", "sensing_interval", "sensing_skip_start"):
        assert key not in state, key


def test_no_unbound_hrefs_on_any_reporting_family():
    for fixture in FAMILIES:
        resources = _load_device(fixture)
        reg = resolve(resources)
        assert reg is not None, fixture
        unbound = []
        discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
        assert unbound == [], f"{fixture}: {unbound}"


# --- the two knobs are separate entities, not one folded control -------------


def test_activation_and_action_are_separate_entities():
    """The resource carries an on/off and an action as independent fields, and
    the appliance's own UI presents them that way. Folding them into one
    control would make a configured action invisible while the feature is off,
    and would leave no way to toggle the feature without overwriting it."""
    assert _desc("periodic_air_sensing").field == (
        "x.com.samsung.da.periodicSensingActivationState"
    )
    assert _desc("sensing_mode").field == "x.com.samsung.da.autoExeState"


def test_action_options_come_from_the_device_not_a_table():
    """supportedAutoExeState is advertised on every reporting fixture, so the
    select reads it rather than carrying a typed-in tuple -- a board adding a
    fourth action is then accepted rather than rejected."""
    desc = _desc("sensing_mode")
    assert desc.options_field == "x.com.samsung.da.supportedAutoExeState"
    assert not desc.options, "options must come from the device, not a static tuple"
    for fixture in FAMILIES:
        assert _rep(fixture)["x.com.samsung.da.supportedAutoExeState"] == [
            "Off",
            "Airpurify",
            "Alarm",
        ], fixture


def test_action_write_sends_the_raw_advertised_value():
    """select.py maps the chosen option back to the device's own casing before
    calling write_fn, so the body is the advertised token verbatim."""
    for raw in ("Off", "Airpurify", "Alarm"):
        href, body = _desc("sensing_mode").write_fn(raw, {})
        assert href == HREF
        assert body == {"x.com.samsung.da.autoExeState": raw}


def test_activation_write_leaves_the_action_alone():
    """Toggling the feature must not disturb autoExeState -- that's what makes
    the switch able to do something the action select can't."""
    for payload, expected in (("On", "On"), ("Off", "Off")):
        href, body = _desc("periodic_air_sensing").write_fn(payload, {})
        assert href == HREF
        assert body == {"x.com.samsung.da.periodicSensingActivationState": expected}
        assert "x.com.samsung.da.autoExeState" not in body


# --- interval ----------------------------------------------------------------


def test_sensing_interval_only_where_the_field_exists():
    """TP1X_DA-AC-AIR (issue #130) omits periodicSensingInterval; the other two
    report it. The entity must follow the field, not the href."""
    assert "sensing_interval" in _state("air_purifier_avt_ww")
    assert "sensing_interval" in _state("air_purifier_vtww")
    assert "sensing_interval" not in _state("air_purifier_tp1x_da_ac_air")


def test_interval_is_minutes_in_the_ui_and_seconds_on_the_wire():
    desc = _desc("sensing_interval")
    assert desc.value_fn("600") == 10
    assert desc.write_fn(10, {})[1] == {"x.com.samsung.da.periodicSensingInterval": "600"}


def test_interval_keeps_zero_distinct_from_unknown():
    """`if secs` would fold a reported 0 into None. Anything else nonzero
    rounds up, so a sub-minute reading can't render as 0 and fall below the
    entity's own floor."""
    desc = _desc("sensing_interval")
    assert desc.value_fn("0") == 0
    assert desc.value_fn("20") == 1
    assert desc.value_fn("61") == 2
    assert desc.value_fn(None) is None


def test_interval_floor_is_one_minute():
    """lastSensingTime lands on an exact minute on this board family, so a
    sub-minute interval is unobservable; and 0 has no established meaning
    here, unlike the zero floors on oven.cook_time / delay_start_hours."""
    desc = _desc("sensing_interval")
    assert desc.native_min == 1
    assert desc.write_fn(0, {}) is None
    assert desc.write_fn(0.4, {}) is None
    assert desc.write_fn(1, {})[1] == {"x.com.samsung.da.periodicSensingInterval": "60"}


# --- skip window -------------------------------------------------------------


def test_skip_time_splits_the_hhmmhhmm_window():
    read_start = _desc("sensing_skip_start").value_fn
    read_end = _desc("sensing_skip_end").value_fn
    # Issue #190's unit ships a real window: 03:00-23:00.
    assert read_start("03002300") == datetime.time(3, 0)
    assert read_end("03002300") == datetime.time(23, 0)
    # Issue #84's unit sits at the inert default.
    assert read_start("00000000") == datetime.time(0, 0)
    # Junk and short strings read as unknown rather than raising.
    assert read_start("") is None
    assert read_start("99999999") is None
    assert read_end("0300") is None


def test_skip_time_write_preserves_the_other_half():
    rep = {"x.com.samsung.da.periodicSensingSkipTime": "03002300"}
    _, body = _desc("sensing_skip_start").write_fn(datetime.time(7, 30), rep)
    assert body == {"x.com.samsung.da.periodicSensingSkipTime": "07302300"}
    _, body = _desc("sensing_skip_end").write_fn(datetime.time(22, 5), rep)
    assert body == {"x.com.samsung.da.periodicSensingSkipTime": "03002205"}
    # A board that has never had a window set still round-trips.
    _, body = _desc("sensing_skip_end").write_fn(datetime.time(1, 2), {})
    assert body == {"x.com.samsung.da.periodicSensingSkipTime": "00000102"}


def test_skip_time_write_normalizes_a_half_it_cannot_parse():
    """Padding alone would splice a malformed half straight back onto the wire.
    The read side already refuses one, so the write side zeroes it instead of
    echoing junk to the device."""
    junk = {"x.com.samsung.da.periodicSensingSkipTime": "0730zzzz"}
    _, body = _desc("sensing_skip_start").write_fn(datetime.time(8, 0), junk)
    assert body == {"x.com.samsung.da.periodicSensingSkipTime": "08000000"}
    junk = {"x.com.samsung.da.periodicSensingSkipTime": "zzzz2200"}
    _, body = _desc("sensing_skip_end").write_fn(datetime.time(23, 0), junk)
    assert body == {"x.com.samsung.da.periodicSensingSkipTime": "00002300"}


def test_skip_status_switch_body():
    assert _desc("periodic_sensing_skip_status").write_fn("On", {})[1] == {
        "x.com.samsung.da.periodicSensingSkipStatus": "On"
    }


# --- read-only diagnostics ---------------------------------------------------


def test_last_sensing_time_reads_as_utc():
    state = _state("air_purifier_avt_ww")
    assert state["last_air_sensing_time"].tzinfo is not None
    assert state["last_air_sensing_time"].year >= 2020


def test_read_only_keys_match_the_range_hood_capability():
    """The three read-only sensor keys are shared with the hood deliberately,
    so both families read from one translation catalog entry each. If either
    side renames one, this catches the drift.

    periodic_air_sensing is excluded: it's a SwitchDesc here and a
    BinarySensorDesc on the hood, so the two sit in different platform
    catalogs and are worded differently -- see the next test.
    """
    from custom_components.localthings.registry.capabilities import range_hood

    hood = {d.key for d in range_hood.AIR_LEVEL_CHECK.entities}
    ours = {d.key for d in air_purifier.AIR_LEVEL_CHECK.entities}
    assert {
        "air_sensing_state",
        "last_air_sensing_time",
        "last_air_sensing_level",
    } <= hood & ours


def test_periodic_air_sensing_is_writable_here_and_read_only_on_hoods():
    """The reason range_hood.AIR_LEVEL_CHECK is not imported directly: the hood
    models this key as a read-only BinarySensorDesc, this board needs a
    writable SwitchDesc. Reusing the hood's capability would migrate every hood
    user's entity to a different platform."""
    from custom_components.localthings.registry.capabilities import range_hood
    from custom_components.localthings.registry.entities import BinarySensorDesc, SwitchDesc

    hood = next(d for d in range_hood.AIR_LEVEL_CHECK.entities if d.key == "periodic_air_sensing")
    assert isinstance(hood, BinarySensorDesc)
    ours = _desc("periodic_air_sensing")
    assert isinstance(ours, SwitchDesc)
    assert ours.write_fn is not None


def test_read_only_diagnostics_match_the_hood_on_visibility():
    """The hood leaves all three enabled; asserting key parity with it while
    hiding two of them would be a quiet divergence."""
    for key in ("air_sensing_state", "last_air_sensing_time", "last_air_sensing_level"):
        assert _desc(key).enabled_default is True, key
