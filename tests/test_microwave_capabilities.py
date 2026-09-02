"""Unit tests for the microwave-family capabilities (issue #121/#66 split
into their own device type instead of being folded into oven.py)."""

from custom_components.localthings.registry.by_type import (
    for_device_by_model,
    resolve,
)
from custom_components.localthings.registry.capabilities import microwave, range_hood
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc, SelectDesc, SwitchDesc

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
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "lamp" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["DeviceType_MW7300B-/EU1", "Sound_Off"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is False


def test_lamp_gated_present_when_lamp_option_reported():
    """Issue #137's plain microwave reports 'Lamp_Off'."""
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "lamp" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["Lamp_Off", "Sound_On"]}
    assert desc.exists_fn is not None
    assert desc.exists_fn(rep, {}) is True


def test_lamp_write_is_single_token():
    """issue #152: the device has never been observed accepting 'On' --
    only 'High'/'Off' -- so the switch's "on" write uses 'High'."""
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "lamp" and isinstance(e, SwitchDesc)
    )
    rep = {"x.com.samsung.da.options": ["Lamp_Off"]}
    assert desc.write_fn is not None
    result = desc.write_fn("On", rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["Lamp_High"]}


def test_lamp_write_requires_existing_options():
    desc = next(
        e
        for e in microwave.MICROWAVE_MODE.entities
        if e.key == "lamp" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    assert desc.write_fn("On", {}) is None


def test_lamp_reads_off_as_false():
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == "lamp")
    assert desc.value_fn(["Lamp_Off"]) is False


def test_lamp_reads_any_non_off_level_as_true():
    """issue #152's ME7500D reports 'Lamp_High', not the binary 'Lamp_On'
    #137's dump implied -- any non-Off/non-absent value must read as on, not
    just a literal 'On'."""
    desc = next(e for e in microwave.MICROWAVE_MODE.entities if e.key == "lamp")
    assert desc.value_fn(["Lamp_High"]) is True


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


def test_me80h2160raa_fixture_resolves_and_has_no_unbound_hrefs():
    from tests.conftest import _load_device

    resources = _load_device("microwave_me80h2160raa")
    reg = resolve(resources, device_types=("oic.wk.d", "oic.d.microwave"))
    assert reg is not None
    assert reg.name == "microwave"

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def _status_entity(key, cls=None):
    return next(
        e
        for e in microwave.MICROWAVE_STATUS.entities
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


def test_status_child_lock_write_is_single_field():
    desc = _status_entity("child_lock", SwitchDesc)
    assert desc.write_fn is not None
    result = desc.write_fn("On", {})
    assert result == (["oven", "status", "vs", "0"], {"childLock": "on"})


def test_status_child_lock_write_rejects_unknown_value():
    desc = _status_entity("child_lock", SwitchDesc)
    assert desc.write_fn is not None
    assert desc.write_fn("maybe", {}) is None


def test_status_cooking_mode_options_reads_live_list_and_admits_current_value():
    desc = _status_entity("cooking_mode", SelectDesc)
    assert callable(desc.options)
    assert desc.value_fn({"name": "NoOperation"}) == "NoOperation"

    # availableModeList never includes the idle sentinel 'NoOperation' --
    # options must union it in, or HA's SelectEntity.state goes Unknown
    # while resting (this device's normal idle state).
    resources = {
        "/oven/status/vs/0": {
            "availableModeList": ["MicroWave", "Autocook", "KeepWarm"],
            "mode": {"name": "NoOperation"},
        }
    }
    assert desc.options(resources) == ["MicroWave", "Autocook", "KeepWarm", "NoOperation"]

    # Already present: not duplicated.
    resources["/oven/status/vs/0"]["mode"] = {"name": "MicroWave"}
    assert desc.options(resources) == ["MicroWave", "Autocook", "KeepWarm"]


def test_status_cooking_mode_write_validates_against_available_modes_and_preserves_recipe():
    desc = _status_entity("cooking_mode", SelectDesc)
    assert desc.write_fn is not None
    rep = {
        "availableModeList": ["MicroWave", "Autocook", "KeepWarm"],
        "mode": {"name": "NoOperation", "indexRecipe": "00000000000000"},
    }
    result = desc.write_fn("MicroWave", rep)
    assert result == (
        ["oven", "status", "vs", "0"],
        {"mode": {"name": "MicroWave", "indexRecipe": "00000000000000"}},
    )
    assert desc.write_fn("Bake", rep) is None


def test_status_power_level_options_reads_live_spec_list():
    desc = _status_entity("power_level", SelectDesc)
    assert callable(desc.options)
    resources = {
        "/oven/spec/vs/0": {
            "cavityInfo": {
                "cavityList": [
                    {
                        "modeSpecList": [
                            {"mode": "Autocook"},
                            {
                                "mode": "MicroWave",
                                "microwavePowerLevel": {
                                    "unit": "percentage",
                                    "powerLevelList": [0, 10, 20, 30, 100],
                                },
                            },
                        ]
                    }
                ]
            }
        }
    }
    assert desc.options(resources) == ["0", "10", "20", "30", "100"]
    assert desc.options({}) == []


def test_status_power_level_write_is_direct_no_hardcoded_step():
    desc = _status_entity("power_level", SelectDesc)
    assert desc.write_fn is not None
    rep = {"microwavePowerLevel": {"unit": "percentage", "setting": 0}}
    result = desc.write_fn("53", rep)
    assert result == (
        ["oven", "status", "vs", "0"],
        {"microwavePowerLevel": {"unit": "percentage", "setting": 53}},
    )
    assert desc.write_fn("not a number", rep) is None


def test_status_power_level_gated_off_without_a_live_options_list():
    """Registering with zero options is a broken, permanently-unusable
    select, not a harmless empty one -- gate the entity off entirely when
    spec is missing or has no MicroWave modeSpec to read (issue #196's
    established rule)."""
    desc = _status_entity("power_level", SelectDesc)
    assert desc.exists_fn is not None
    assert desc.exists_fn({}, {}) is False
    resources = {
        "/oven/spec/vs/0": {
            "cavityInfo": {
                "cavityList": [
                    {
                        "modeSpecList": [
                            {
                                "mode": "MicroWave",
                                "microwavePowerLevel": {"powerLevelList": [0, 100]},
                            }
                        ]
                    }
                ]
            }
        }
    }
    assert desc.exists_fn({}, resources) is True


def test_status_power_level_value_is_none_not_the_string_none():
    desc = _status_entity("power_level", SelectDesc)
    assert desc.value_fn({"unit": "percentage", "setting": 50}) == "50"
    assert desc.value_fn({"unit": "percentage"}) is None
    assert desc.value_fn(None) is None


def test_status_cook_time_write_rejects_out_of_range():
    desc = _status_entity("cook_time", NumberDesc)
    assert desc.write_fn is not None
    rep = {"time": {"setting": 0}}
    result = desc.write_fn(90, rep)
    assert result == (["oven", "status", "vs", "0"], {"time": {"setting": 90}})
    # 0 is valid, not clamped to modeSpec's stated floor of 1 -- it's the
    # device's own live idle value, and rejecting it would make the timer
    # unclearable via this entity.
    assert desc.write_fn(0, rep) == (["oven", "status", "vs", "0"], {"time": {"setting": 0}})
    assert desc.write_fn(-1, rep) is None
    assert desc.write_fn(6040, rep) is None


def test_status_cook_time_write_is_single_field_not_rmw():
    """issue #54 convention: carry only the changed token. `time` also
    carries device-computed operation/remaining/completion that a plain
    RMW would echo back stale mid-cycle."""
    desc = _status_entity("cook_time", NumberDesc)
    assert desc.write_fn is not None
    rep = {"time": {"setting": 5, "operation": "run", "remaining": 4, "completion": "x"}}
    result = desc.write_fn(90, rep)
    assert result == (["oven", "status", "vs", "0"], {"time": {"setting": 90}})


def test_status_cook_time_remaining_reads_countdown():
    desc = _status_entity("cook_time_remaining")
    assert desc.value_fn({"setting": 90, "remaining": 42}) == 42
    assert desc.value_fn(None) is None


def test_status_cook_finish_time_parses_iso_and_blanks_to_none():
    desc = _status_entity("cook_finish_time")
    result = desc.value_fn({"completion": "2026-09-01T23:14:23"})
    assert result is not None
    assert result.isoformat() == "2026-09-01T23:14:23+00:00"
    assert desc.value_fn({"completion": ""}) is None
    assert desc.value_fn(None) is None


def test_settings_has_no_orphan_unit_format_sensors():
    """weightUnit/timeFormat have no live value to unit-ify or format on
    this board (no weight-bearing entity, no HA-rendered field that reads
    a device clock-format hint) -- a bare passthrough sensor would just be
    a raw string nothing else on the device relates to."""
    keys = {e.key for e in microwave.MICROWAVE_SETTINGS.entities}
    assert "weight_unit" not in keys
    assert "time_format" not in keys


def test_settings_beep_reads_and_writes():
    desc = next(
        e
        for e in microwave.MICROWAVE_SETTINGS.entities
        if e.key == "beep" and isinstance(e, SwitchDesc)
    )
    assert desc.value_fn("on") is True
    assert desc.value_fn("off") is False
    assert desc.write_fn is not None
    result = desc.write_fn("Off", {})
    assert result == (["oven", "settings", "status", "vs", "0"], {"beepSound": "off"})


def test_hood_status_fan_speed_options_excludes_unavailable_and_placeholder():
    resources = {
        "/hood/spec/vs/0": {"fanSpeedList": ["off", "low", "medium", "high", "boost"]},
        "/hood/status/vs/0": {"unavailableFanSpeedList": ["boost"]},
    }
    desc = next(
        e
        for e in range_hood.HOOD_STATUS.entities
        if e.key == "hood_fan_speed" and isinstance(e, SelectDesc)
    )
    assert callable(desc.options)
    assert desc.options(resources) == ["off", "low", "medium", "high"]

    # The dump's own [''] placeholder must not exclude a real speed.
    resources["/hood/status/vs/0"] = {"unavailableFanSpeedList": [""]}
    assert desc.options(resources) == ["off", "low", "medium", "high", "boost"]


def test_hood_status_fan_speed_and_lamp_gated_off_without_spec():
    fan_desc = next(
        e
        for e in range_hood.HOOD_STATUS.entities
        if e.key == "hood_fan_speed" and isinstance(e, SelectDesc)
    )
    lamp_desc = next(
        e
        for e in range_hood.HOOD_STATUS.entities
        if e.key == "hood_lamp" and isinstance(e, SelectDesc)
    )
    assert fan_desc.exists_fn is not None
    assert lamp_desc.exists_fn is not None
    assert fan_desc.exists_fn({}, {}) is False
    assert lamp_desc.exists_fn({}, {}) is False

    resources = {
        "/hood/spec/vs/0": {
            "fanSpeedList": ["off", "low"],
            "lampStateList": ["off", "on"],
        }
    }
    assert fan_desc.exists_fn({}, resources) is True
    assert lamp_desc.exists_fn({}, resources) is True


def test_hood_status_fan_speed_write():
    desc = next(
        e
        for e in range_hood.HOOD_STATUS.entities
        if e.key == "hood_fan_speed" and isinstance(e, SelectDesc)
    )
    assert desc.write_fn is not None
    assert desc.write_fn("high", {}) == (["hood", "status", "vs", "0"], {"fanSpeed": "high"})


def test_hood_status_lamp_options_reads_spec():
    resources = {"/hood/spec/vs/0": {"lampStateList": ["off", "medium", "on"]}}
    desc = next(
        e
        for e in range_hood.HOOD_STATUS.entities
        if e.key == "hood_lamp" and isinstance(e, SelectDesc)
    )
    assert callable(desc.options)
    assert desc.options(resources) == ["off", "medium", "on"]


def test_hood_status_grease_filter_alarm_detects_any_active_alarm():
    desc = next(e for e in range_hood.HOOD_STATUS.entities if e.key == "grease_filter_alarm")
    assert desc.value_fn([{"filterType": "greaseFilter", "alarm": "off"}]) is False
    assert desc.value_fn([{"filterType": "greaseFilter", "alarm": "on"}]) is True
    assert desc.value_fn(None) is False
    # An explicit JSON null alarm field must not read as active --
    # str(None).lower() is 'none', which isn't 'off' either.
    assert desc.value_fn([{"filterType": "greaseFilter", "alarm": None}]) is False
    assert desc.value_fn([{"filterType": "greaseFilter"}]) is False
