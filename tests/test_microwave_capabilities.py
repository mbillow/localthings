"""Unit tests for the microwave-family capabilities (issue #121/#66 split
into their own device type instead of being folded into oven.py)."""

from custom_components.localthings.registry.by_type import (
    for_device_by_model,
    resolve,
)
from custom_components.localthings.registry.capabilities import microwave, range_hood
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    BinarySensorDesc,
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


# ---------------------------------------------------------------------------
# DAWIT 3.0 generation (issue #433) -- /oven/status/vs/0, /oven/spec/vs/0,
# /oven/settings/status/vs/0, and the analogous built-in vent hood.
# ---------------------------------------------------------------------------


def _me80h2160raa():
    from tests.conftest import _load_device

    resources = _load_device("microwave_me80h2160raa")
    reg = resolve(resources, device_types=("oic.wk.d", "oic.d.microwave"))
    return reg, resources


def test_me80h2160raa_fixture_resolves_and_has_no_unbound_hrefs():
    reg, resources = _me80h2160raa()
    assert reg is not None
    assert reg.name == "microwave"

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_dawit_generation_is_entirely_read_only():
    """Issue #433's reporter got CoAP 4.05 (Method Not Allowed) writing
    every one of this generation's five resources, and the board declares
    them oic.if.s where every writable href in the corpus declares
    oic.if.a. So none of these descriptors may carry a write_fn -- a
    control that always errors is worse than a sensor (issues #181/#183,
    common.KIDS_LOCK_VS_FALLBACK)."""
    reg, resources = _me80h2160raa()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    writable = sorted({b.desc.key for b in bound if getattr(b.desc, "write_fn", None) is not None})
    assert writable == []


def _status_entity(key, cls=None):
    return next(
        e
        for e in microwave.MICROWAVE_STATUS.entities
        if e.key == key and (cls is None or isinstance(e, cls))
    )


def _hood_entity(key, cls=None):
    return next(
        e
        for e in range_hood.HOOD_STATUS.entities
        if e.key == key and (cls is None or isinstance(e, cls))
    )


def test_status_machine_state_maps_operation_to_ocf():
    desc = _status_entity("machine_state")
    assert desc.value_fn("ready") == "idle"
    assert desc.value_fn("run") == "active"
    assert desc.value_fn("pause") == "pause"


def test_status_cycle_active_reflects_operation():
    desc = _status_entity("cycle_active")
    assert desc.value_fn("run") is True
    assert desc.value_fn("ready") is False


def test_status_door_open_reads_nested_state():
    desc = _status_entity("door_open")
    assert desc.value_fn({"state": "open"}) is True
    assert desc.value_fn({"state": "closed"}) is False
    assert desc.value_fn(None) is False


def test_status_child_lock_polarity_matches_the_shared_lock_sensor():
    """device_class='lock' is inverted in HA -- On means unlocked, the
    same reading common.KIDS_LOCK_GENERIC ships."""
    desc = _status_entity("child_lock", BinarySensorDesc)
    assert desc.device_class == "lock"
    assert desc.value_fn("off") is True
    assert desc.value_fn("on") is False


def test_status_cooking_mode_uses_the_shared_catalog_states():
    """The device's PascalCase mode names map onto the state keys
    select.cooking_mode already ships, so this generation reads the same
    as the older one."""
    desc = _status_entity("cooking_mode")
    assert desc.value_fn({"name": "MicroWave"}) == "micro_wave"
    assert desc.value_fn({"name": "NoOperation"}) == "no_operation"
    assert desc.value_fn(None) is None


def test_status_cooking_mode_options_read_the_live_list():
    """availableModeList, mapped the same way -- no hardcoded vocabulary.
    It omits NoOperation (mode.name's resting value); sensor.py's
    `options` property admits the live value, so nothing unions it here."""
    desc = _status_entity("cooking_mode")
    resources = {
        "/oven/status/vs/0": {
            "availableModeList": ["MicroWave", "Autocook", "KeepWarm"],
            "mode": {"name": "NoOperation"},
        }
    }
    assert desc.options(resources) == ["micro_wave", "autocook", "keep_warm"]
    assert desc.options({}) == []


def test_status_power_level_takes_its_unit_from_the_device():
    desc = _status_entity("power_level")
    assert desc.value_fn({"unit": "percentage", "setting": 60}) == 60
    assert desc.value_fn({"unit": "percentage"}) is None
    assert desc.unit_fn({"microwavePowerLevel": {"unit": "percentage"}}) == "%"
    assert desc.unit_fn({"microwavePowerLevel": {"unit": "grams"}}) is None
    assert desc.unit_fn({}) is None


def test_status_cook_time_reads_seconds():
    """/oven/spec/vs/0 caps `time` at 6039 = 99*60 + 99, the 99:99 these
    panels count down from -- which is what fixes the unit as seconds."""
    setting = _status_entity("cook_time")
    remaining = _status_entity("cook_time_remaining")
    assert (setting.unit, setting.device_class) == ("s", "duration")
    assert setting.value_fn({"setting": 90, "remaining": 45}) == 90
    assert remaining.value_fn({"setting": 90, "remaining": 45}) == 45
    assert remaining.value_fn({}) is None


def test_status_cook_finish_time_parses_iso_and_blanks_to_none():
    desc = _status_entity("cook_finish_time")
    assert desc.value_fn({"completion": ""}) is None
    assert desc.value_fn({}) is None
    parsed = desc.value_fn({"completion": "2026-09-01T23:14:23"})
    assert parsed is not None and parsed.year == 2026


def test_settings_are_binary_sensors_not_switches():
    keys = {e.key for e in microwave.MICROWAVE_SETTINGS.entities}
    assert keys == {"beep", "remind_beep", "display_time_auto_sync"}
    for desc in microwave.MICROWAVE_SETTINGS.entities:
        assert isinstance(desc, BinarySensorDesc), desc.key
        assert desc.value_fn("on") is True
        assert desc.value_fn("off") is False


def test_settings_has_no_orphan_unit_format_sensors():
    """weightUnit/timeFormat are deliberately unbound -- see the
    capability's comment."""
    keys = {e.key for e in microwave.MICROWAVE_SETTINGS.entities}
    assert "weight_unit" not in keys
    assert "time_format" not in keys


def test_hood_options_read_the_spec_resource():
    """The status rep carries no vocabulary of its own; both lists live on
    the sibling /hood/spec/vs/0."""
    resources = {
        "/hood/spec/vs/0": {
            "fanSpeedList": ["off", "low", "medium", "high", "boost"],
            "lampStateList": ["off", "medium", "on"],
        }
    }
    assert _hood_entity("hood_fan_speed").options(resources) == [
        "off",
        "low",
        "medium",
        "high",
        "boost",
    ]
    assert _hood_entity("hood_lamp").options(resources) == ["off", "medium", "on"]


def test_hood_fan_speed_options_keep_currently_unavailable_speeds():
    """unavailableFanSpeedList says what can't be *selected* right now.
    Nothing here is selectable, and an enum sensor still has to render
    whatever the device reports, so the list isn't subtracted."""
    resources = {
        "/hood/spec/vs/0": {"fanSpeedList": ["off", "low", "boost"]},
        "/hood/status/vs/0": {"unavailableFanSpeedList": ["boost"], "fanSpeed": "boost"},
    }
    assert _hood_entity("hood_fan_speed").options(resources) == ["off", "low", "boost"]


def test_hood_entities_gated_off_without_spec():
    """An enum sensor with no options is a broken entity in HA, so both
    stand down on a board reporting status without its spec sibling."""
    status = {"fanSpeed": "off", "lamp": "off"}
    for key in ("hood_fan_speed", "hood_lamp"):
        desc = _hood_entity(key)
        assert desc.exists_fn(status, {"/hood/status/vs/0": status}) is False
        assert (
            desc.exists_fn(
                status,
                {
                    "/hood/status/vs/0": status,
                    "/hood/spec/vs/0": {
                        "fanSpeedList": ["off", "low"],
                        "lampStateList": ["off", "on"],
                    },
                },
            )
            is True
        )


def test_hood_grease_filter_alarm_detects_any_active_alarm():
    desc = _hood_entity("grease_filter_alarm")
    assert desc.value_fn([{"filterType": "greaseFilter", "alarm": "off"}]) is False
    assert desc.value_fn([{"filterType": "greaseFilter", "alarm": "on"}]) is True
    # A null/blank alarm is no alarm -- str(None).lower() is 'none', which
    # a bare != "off" check would read as active.
    assert desc.value_fn([{"filterType": "greaseFilter", "alarm": None}]) is False
    assert desc.value_fn([{"filterType": "greaseFilter"}]) is False
    assert desc.value_fn([]) is False
    assert desc.value_fn(None) is False


def test_hood_front_vent_reads_raw_on_off():
    desc = _hood_entity("front_vent_open")
    assert desc.value_fn("on") is True
    assert desc.value_fn("off") is False
