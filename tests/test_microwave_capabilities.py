"""Unit tests for the microwave-family capabilities (issue #121/#66 split
into their own device type instead of being folded into oven.py)."""

from custom_components.localthings.registry.by_type import (
    for_device_by_model,
    resolve,
)
from custom_components.localthings.registry.capabilities import microwave
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    LightDesc,
    NumberDesc,
    SelectDesc,
    SwitchDesc,
)

# ---------------------------------------------------------------------------
# Device-type detection + full-dump coverage
# ---------------------------------------------------------------------------


def test_microwave_fixture_resolves_and_has_no_unbound_hrefs():
    from tests.conftest import _load_device

    resources = _load_device("microwave_mw7300b")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None
    assert reg.name == "microwave"

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_microwave_hood_fan_fixture_resolves_and_has_no_unbound_hrefs():
    """Issues #137/#142: `/hood/fanspeed/vs/0` (the combi unit's built-in
    vent fan) was previously unbound on this family."""
    from tests.conftest import _load_device

    resources = _load_device("microwave_me7500d")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None
    assert reg.name == "microwave"

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_qooker_fixture_resolves_as_microwave_and_has_no_unbound_hrefs():
    """Bespoke Qooker MW7500A is microwave-shaped despite both its OCF type
    and internal board token saying oven."""
    from tests.conftest import _load_device

    resources = _load_device("qooker_mw7500a")

    reg = resolve(resources, device_types=("oic.wk.d", "oic.d.oven"))

    assert reg is not None
    assert reg.name == "microwave"
    unbound = []
    bound = discover(
        resources,
        reg.capabilities,
        reg.pattern_capabilities,
        log=unbound.append,
    )
    assert unbound == []
    keys = {entity.desc.key for entity in bound}
    assert {"cooking_mode", "power_level", "setpoint"} <= keys
    assert "oven_mode" not in keys


# ---------------------------------------------------------------------------
# MICROWAVE_SETPOINT — NumberDesc with RMW write semantics
# ---------------------------------------------------------------------------


def _microwave_setpoint_desc():
    return next(e for e in microwave.MICROWAVE_SETPOINT.entities if isinstance(e, NumberDesc))


def test_microwave_setpoint_write_is_read_modify_write():
    desc = _microwave_setpoint_desc()
    rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.desired": "0"}]}
    assert desc.write_fn is not None
    result = desc.write_fn(180, rep)
    assert result is not None
    path, body = result
    assert path == ["temperatures", "vs", "0"]
    assert body["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"] == "180"


def test_microwave_setpoint_rmw_preserves_other_item_fields():
    desc = _microwave_setpoint_desc()
    rep = {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.current": "150",
                "x.com.samsung.da.desired": "150",
            }
        ]
    }
    assert desc.write_fn is not None
    result = desc.write_fn(180, rep)
    assert result is not None
    _path, body = result
    item = body["x.com.samsung.da.items"][0]
    assert item["x.com.samsung.da.desired"] == "180"
    assert item["x.com.samsung.da.current"] == "150"


def test_microwave_setpoint_clamps_to_step():
    desc = _microwave_setpoint_desc()
    rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.desired": "0"}]}
    assert desc.write_fn is not None
    result = desc.write_fn(182, rep)  # nearest 5 = 180
    assert result is not None
    _, body = result
    assert body["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"] == "180"


def test_microwave_setpoint_rejects_out_of_range():
    desc = _microwave_setpoint_desc()
    rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.desired": "100"}]}
    assert desc.write_fn is not None
    assert desc.write_fn(20, rep) is None  # below min (40)
    assert desc.write_fn(210, rep) is None  # above max (200)


def test_microwave_setpoint_rejects_missing_items():
    desc = _microwave_setpoint_desc()
    assert desc.write_fn is not None
    assert desc.write_fn(180, {}) is None


def test_microwave_setpoint_exists_only_for_celsius():
    """No Fahrenheit dump exists for this family (unlike oven.py's, verified
    against issue #44) -- the writable setpoint stays hidden rather than
    showing unverified bounds under the wrong unit."""
    desc = _microwave_setpoint_desc()
    celsius_rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.unit": "Celsius"}]}
    fahrenheit_rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.unit": "Fahrenheit"}]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(celsius_rep, {}) is True
    assert desc.exists_fn(fahrenheit_rep, {}) is False


# ---------------------------------------------------------------------------
# MICROWAVE_CAVITY — power_level sensor
# ---------------------------------------------------------------------------


def test_power_level_parses_watt_suffix():
    """Issue #121's combi dump reports e.g. '0W'."""
    desc = next(e for e in microwave.MICROWAVE_CAVITY.entities if e.key == "power_level")
    assert desc.value_fn("900W") == 900


def test_power_level_parses_bare_number():
    """Issue #137's plain microwave reports the bare number, no 'W' suffix."""
    desc = next(e for e in microwave.MICROWAVE_CAVITY.entities if e.key == "power_level")
    assert desc.value_fn("0") == 0


def test_power_level_handles_missing_value():
    desc = next(e for e in microwave.MICROWAVE_CAVITY.entities if e.key == "power_level")
    assert desc.value_fn(None) is None


# ---------------------------------------------------------------------------
# MICROWAVE_MODE — SelectDesc with non-empty, family-specific options
# ---------------------------------------------------------------------------


def _microwave_cooking_mode_desc():
    return next(e for e in microwave.MICROWAVE_MODE.entities if isinstance(e, SelectDesc))


def _microwave_lamp_desc():
    return next(e for e in microwave.MICROWAVE_MODE.entities if isinstance(e, LightDesc))


def test_microwave_mode_options_nonempty():
    desc = _microwave_cooking_mode_desc()
    assert callable(desc.options)
    options = desc.options({})
    assert len(options) > 0
    assert "MicroWave" in options
    assert "AirFryer" in options  # distinct spelling from oven.py's 'AirFry'


def test_microwave_mode_options_reads_live_supported_modes():
    """issue #152's ME7500D reports only 4 of the 11 union-of-all-dumps
    _MICROWAVE_MODES -- the live supportedModes list is used verbatim when
    present, same live-first pattern as oven._oven_mode_options, instead of
    offering users modes their own unit doesn't have."""
    desc = _microwave_cooking_mode_desc()
    resources = {
        "/mode/vs/0": {
            "x.com.samsung.da.supportedModes": ["NoOperation", "MicroWave", "Autocook", "KeepWarm"],
        }
    }
    assert desc.options(resources) == ["NoOperation", "MicroWave", "Autocook", "KeepWarm"]


def test_microwave_mode_write_round_trips():
    desc = _microwave_cooking_mode_desc()
    assert desc.write_fn is not None
    result = desc.write_fn("MicroWave", {})
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body["x.com.samsung.da.modes"] == ["MicroWave"]


def test_microwave_mode_rejects_unknown():
    desc = _microwave_cooking_mode_desc()
    assert desc.write_fn is not None
    assert desc.write_fn("SpaghettiMode", {}) is None


def test_microwave_mode_write_validates_against_live_supported_modes():
    """A device reporting its own supportedModes is validated against that
    list, not the static union-of-all-dumps fallback -- 'AirFryer' is a
    valid _MICROWAVE_MODES entry but must still be rejected for a unit
    whose own supportedModes doesn't include it."""
    desc = _microwave_cooking_mode_desc()
    rep = {"x.com.samsung.da.supportedModes": ["NoOperation", "MicroWave", "Autocook", "KeepWarm"]}
    assert desc.write_fn is not None
    result = desc.write_fn("MicroWave", rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body["x.com.samsung.da.modes"] == ["MicroWave"]
    assert desc.write_fn("AirFryer", rep) is None


# ---------------------------------------------------------------------------
# MICROWAVE_MODE — lamp/sound options-array writes
# ---------------------------------------------------------------------------


def test_sound_write_is_single_token():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "sound" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["Sound_On"]}
    assert desc.write_fn is not None
    result = desc.write_fn("Off", rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["Sound_Off"]}


def test_lamp_gated_absent_when_no_lamp_option():
    """Issue #121's combi dump has no 'Lamp_*' token at all -- unlike
    oven.py's lamp switch (assumed universal), this one self-gates off."""
    desc = _microwave_lamp_desc()
    rep = {"x.com.samsung.da.options": ["DeviceType_MW7300B-/EU1", "Sound_Off"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is False


def test_lamp_gated_present_when_lamp_option_reported():
    """Issue #137's plain microwave reports 'Lamp_Off'."""
    desc = _microwave_lamp_desc()
    rep = {"x.com.samsung.da.options": ["Lamp_Off", "Sound_On"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is True


def test_lamp_high_write_is_single_token():
    desc = _microwave_lamp_desc()
    rep = {"x.com.samsung.da.options": ["Lamp_Off"]}
    assert desc.write_fn is not None
    result = desc.write_fn(255, rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["Lamp_High"]}


def test_lamp_low_write_is_single_token():
    desc = _microwave_lamp_desc()
    rep = {"x.com.samsung.da.options": ["Lamp_High"]}
    assert desc.write_fn is not None
    result = desc.write_fn(128, rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["Lamp_Low"]}


def test_lamp_off_write_is_single_token():
    desc = _microwave_lamp_desc()
    rep = {"x.com.samsung.da.options": ["Lamp_High"]}
    assert desc.write_fn is not None
    result = desc.write_fn(0, rep)
    assert result is not None
    _path, body = result
    assert body == {"x.com.samsung.da.options": ["Lamp_Off"]}


def test_lamp_write_requires_existing_options():
    desc = _microwave_lamp_desc()
    assert desc.write_fn is not None
    assert desc.write_fn(255, {}) is None


def test_lamp_reads_off_as_zero_brightness():
    desc = _microwave_lamp_desc()
    assert desc.value_fn(["Lamp_Off"]) == 0


def test_lamp_reads_low_and_high_brightness():
    desc = _microwave_lamp_desc()
    assert desc.value_fn(["Lamp_Low"]) == 128
    assert desc.value_fn(["Lamp_High"]) == 255


def test_lamp_reads_unknown_level_as_unknown():
    desc = _microwave_lamp_desc()
    assert desc.value_fn(["Lamp_Night"]) is None


# ---------------------------------------------------------------------------
# MICROWAVE_MODE — filter_remind/remind_beep options-array writes (issue #181)
# ---------------------------------------------------------------------------


def test_filter_remind_gated_absent_when_no_option():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "filter_remind" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["DeviceType_MW7300B-/EU1", "Sound_Off"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is False


def test_filter_remind_gated_present_when_option_reported():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "filter_remind" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["FilterRemind_Off"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is True


def test_filter_remind_reads_on_off():
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == "filter_remind")
    assert desc.value_fn(["FilterRemind_On"]) is True
    assert desc.value_fn(["FilterRemind_Off"]) is False


def test_filter_remind_write_is_single_token():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "filter_remind" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["FilterRemind_Off"]}
    assert desc.write_fn is not None
    result = desc.write_fn("On", rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["FilterRemind_On"]}


def test_filter_remind_write_requires_existing_options():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "filter_remind" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    assert desc.write_fn("On", {}) is None


def test_remind_beep_gated_absent_when_no_option():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "remind_beep" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["DeviceType_MW7300B-/EU1", "Sound_Off"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is False


def test_remind_beep_gated_present_when_option_reported():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "remind_beep" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["RemindBeep_On"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is True


def test_remind_beep_reads_on_off():
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == "remind_beep")
    assert desc.value_fn(["RemindBeep_On"]) is True
    assert desc.value_fn(["RemindBeep_Off"]) is False


def test_remind_beep_write_is_single_token():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "remind_beep" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["RemindBeep_On"]}
    assert desc.write_fn is not None
    result = desc.write_fn("Off", rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["RemindBeep_Off"]}


def test_remind_beep_write_requires_existing_options():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "remind_beep" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    assert desc.write_fn("On", {}) is None
