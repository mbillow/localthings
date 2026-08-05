"""Tests for Samsung air-conditioner support (issue #17).

These stay HA-free like the rest of the suite: they exercise the registry,
discovery/flatten, and the CLIMATE capability's write contract. The composite
climate entity itself lives in climate.py (imports homeassistant) and is not
importable here -- consistent with how the other HA platform files are untested.
"""

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import airconditioner
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    ClimateDesc,
    SelectDesc,
    SensorDesc,
    SwitchDesc,
)
from tests.conftest import _load_device


def _ac():
    resources = _load_device("airconditioner")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _resolve(name):
    """Mirror the coordinator's detection: board tokens in modelNum."""
    resources = _load_device(name)
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def _bound():
    reg, resources = _ac()
    return discover(resources, reg.capabilities, reg.pattern_capabilities), resources


def _state():
    bound, resources = _bound()
    return flatten(bound, resources)


def test_ac_model_resolves_to_airconditioner_registry():
    reg, _ = _ac()
    assert reg is not None and reg.name == "airconditioner"


def test_no_unbound_hrefs():
    """Every resource in the issue #17 dump binds or is covered -- clears the
    coverage-gap repair."""
    reg, resources = _ac()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_climate_entity_is_bound():
    """The composite climate entity binds the primary /mode/vs/0 resource."""
    bound, _ = _bound()
    climate = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climate) == 1
    assert climate[0].href == "/mode/vs/0"


def test_expected_state_keys_present():
    state = _state()
    for key in (
        "climate",
        "air_purify",
        "auto_clean",
        "air_filter_status",
        "air_filter_usage",
        "diagnosis_status",
        "alarm_code",
        "energy_kwh",
    ):
        assert key in state, key


def test_power_and_convenient_folded_into_climate():
    """On/off is the climate entity's HVACMode.OFF and convenient mode is its
    preset_mode -- neither surfaces as a standalone switch/select."""
    state = _state()
    assert "power_switch" not in state
    assert "convenient_mode" not in state


def test_air_filter_usage_is_percentage_of_capacity():
    """filterUsage is a raw count in the capacity unit (100 of 500), surfaced as
    a percentage rather than the misleading raw value."""
    assert _state()["air_filter_usage"] == 20


def test_climate_write_targets():
    """The CLIMATE write_fn maps each (kind, value) command to the right vendor
    POST target and body. `value` is already the raw device code. Power and
    temperature target the vendor /power/vs/0 and /temperatures/vs/0 (the OCF
    /power/0 is absent on most boards and a non-authoritative mirror where
    present; /temperature/desired/0 is only written via the temperature_ocf
    kind, on boards that have the OCF pair)."""
    climate_desc = next(e for e in airconditioner.CLIMATE.entities if isinstance(e, ClimateDesc))
    assert climate_desc.write_fn is not None
    write = climate_desc.write_fn
    assert write(("power", True), {}) == (["power", "vs", "0"], {"x.com.samsung.da.power": "On"})
    assert write(("power", False), {}) == (["power", "vs", "0"], {"x.com.samsung.da.power": "Off"})
    assert write(("mode", "Heat"), {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.modes": ["Heat"]},
    )
    # OCF-pair boards: temperature_ocf -> /temperature/desired/0.
    assert write(("temperature_ocf", 23.6), {}) == (
        ["temperature", "desired", "0"],
        {"temperature": 23.6},
    )
    # Vendor boards: temperature -> /temperatures/vs/0, carrying only the id
    # and the changed field -- the device merges current/min/max/unit itself
    # (see common.merge_items_field, wired into async_send_command, for the
    # read-side half that keeps the optimistic cache complete).
    assert write(("temperature", 22), {}) == (
        ["temperatures", "vs", "0"],
        {
            "x.com.samsung.da.items": [
                {"x.com.samsung.da.id": "0", "x.com.samsung.da.desired": "22"}
            ]
        },
    )
    assert write(("fan", "2"), {}) == (
        ["wind", "strength", "vs", "0"],
        {"x.com.samsung.da.modes": "2"},
    )
    assert write(("swing", "All"), {}) == (
        ["wind", "direction", "vs", "0"],
        {"x.com.samsung.da.modes": "All"},
    )
    assert write(("preset", "Sleep"), {}) == (
        ["mode", "convenient", "vs", "0"],
        {"x.com.samsung.da.modes": "Sleep"},
    )
    assert write(("bogus", 1), {}) is None


def test_climate_write_preserves_half_degree_temperature_steps():
    climate_desc = next(e for e in airconditioner.CLIMATE.entities if isinstance(e, ClimateDesc))
    write = climate_desc.write_fn
    resources = {
        "/temperature/control/vs/0": {"x.com.samsung.da.increment": "0.5"},
        "/temperatures/vs/0": {"x.com.samsung.da.increment": "0.5"},
    }
    assert write(("temperature_ocf", 24.5), {}, None, resources) == (
        ["temperature", "desired", "0"],
        {"temperature": 24.5},
    )
    assert write(("temperature", 24.5), {}, None, resources) == (
        ["temperatures", "vs", "0"],
        {
            "x.com.samsung.da.items": [
                {"x.com.samsung.da.id": "0", "x.com.samsung.da.desired": "24.5"}
            ]
        },
    )


def test_climate_consumed_hrefs_declared_as_coverage():
    """The climate-consumed and ambiguous hrefs are declared in the AC registry
    (as no-entity coverage caps) so they don't leak as gaps -- but produce no
    standalone entities. /temperature/current/0 and /temperatures/vs/0 are
    NOT in this list -- CURRENT_TEMPERATURE / CURRENT_TEMPERATURE_VS give
    those two real sensor entities (issue #75). /sensors/vs/0 is also NOT
    here -- AIR_QUALITY gives it real entity sensors."""
    reg, _ = _ac()
    for href in (
        "/power/0",
        "/power/vs/0",
        "/temperature/desired/0",
        "/wind/strength/vs/0",
        "/mode/convenient/vs/0",
        "/humidity/0",
    ):
        caps = reg.capabilities.get(href)
        assert caps, href
        assert all(c.entities == () for c in caps), href


# ---------------------------------------------------------------------------
# TP1X_DA-AC-RAC-01011 (oneUiVersion "7.0 Air conditioner", Tizen Lite) -- a
# newer model class than the ARTIK051_PRAC dump above. It has no OCF-standard
# /temperature/current+desired pair (temperature lives on the vendor
# /temperatures/vs/0 items[] resource), exposes a /light/vs/0 display light, and
# carries extra vendor housekeeping hrefs. Issue #17 for this class (PR #36).
# ---------------------------------------------------------------------------


def _ac_tp1x():
    return _resolve("airconditioner_tp1x_da_ac_rac_01011")


def test_tp1x_resolves_to_airconditioner_registry():
    reg, _ = _ac_tp1x()
    assert reg is not None and reg.name == "airconditioner"


def test_tp1x_no_unbound_hrefs():
    """Every resource in the TP1X dump binds or is covered -- including
    /temperatures/vs/0, /light/vs/0 and the housekeeping hrefs absent from
    the ARTIK051 dump. Clears the coverage-gap repair."""
    reg, resources = _ac_tp1x()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_tp1x_display_light_switch_present():
    """/light/vs/0 (mode On/Off) surfaces as the display-light switch."""
    reg, resources = _ac_tp1x()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state.get("display_light") is True  # device reports mode == 'On'


def test_tp1x_vendor_temperature_and_light_covered():
    """The vendor temperature resource (read by the climate entity) and the
    display-light resource both resolve in the registry -- no gap."""
    reg, _ = _ac_tp1x()
    assert reg.capabilities.get("/temperatures/vs/0"), "/temperatures/vs/0"
    assert reg.capabilities.get("/light/vs/0"), "/light/vs/0"


def test_tp1x_climate_entity_is_bound():
    """The composite climate entity still binds the primary /mode/vs/0."""
    reg, resources = _ac_tp1x()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    climate = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climate) == 1 and climate[0].href == "/mode/vs/0"


def test_tp2x_rac_20k_model_resolves_via_model_fallback():
    """TP2X_RAC_20K (issue #37) reports no oneUiVersion and no '_PRAC_' token
    -- resolved via the '_RAC_' modelNum fallback added for this device."""
    reg, _ = _resolve("airconditioner_tp2x_rac_20k")
    assert reg is not None and reg.name == "airconditioner"


def test_tp2x_rac_20k_no_unbound_hrefs():
    reg, resources = _resolve("airconditioner_tp2x_rac_20k")
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_tp1x_rac_model_resolves_via_rac_token():
    """TP1X_DA-AC-RAC-01001_0000 (issue #38). It self-reports oneUiVersion
    '7.0 Air conditioner', which used to be what resolved it; the hyphenated
    '-RAC-' board token types it now, so firmware that omits oneUiVersion (the
    cool-only variant, issue #91) lands on the same registry."""
    reg, _ = _resolve("airconditioner_tp1x_rac")
    assert reg is not None and reg.name == "airconditioner"


def test_tp1x_rac_no_unbound_hrefs():
    reg, resources = _resolve("airconditioner_tp1x_rac")
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_tp1x_rac_expected_state_keys_present():
    reg, resources = _resolve("airconditioner_tp1x_rac")
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    for key in (
        "climate",
        "display_light",
        "mute_once",
        "selfcheck_status",
        "selfcheck_result",
        "current_limit_enabled",
        "current_limit_level",
    ):
        assert key in state, key


def test_caww_tp2_model_resolves_via_model_fallback():
    """A-CAWW-TP2-20-COMMON (issue #52, System AC) reports no oneUiVersion
    and no '_RAC_'/'_PRAC_' token -- resolved via the '-CAWW-' modelNum
    fallback added for this device."""
    reg, _ = _resolve("airconditioner_caww_tp2")
    assert reg is not None and reg.name == "airconditioner"


def test_caww_tp2_no_unbound_hrefs():
    """Every resource in the issue #52 dump binds or is ignored -- clears
    the coverage-gap repair. Only new href beyond the existing RAC/PRAC
    surface is /sac/installationinfo/vs/0 (opaque SAC installation topology,
    ignored)."""
    reg, resources = _resolve("airconditioner_caww_tp2")
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_caww_tp2_sac_installationinfo_is_ignored():
    ignored_hrefs = {cap.href for cap in airconditioner.COVERAGE}
    assert "/sac/installationinfo/vs/0" in ignored_hrefs


def test_mute_once_write_target():
    desc = next(e for e in airconditioner.MUTE_ONCE.entities if isinstance(e, SwitchDesc))
    assert desc.write_fn is not None
    write = desc.write_fn
    assert write("On", {}) == (["option", "muteonce", "vs", "0"], {"muteonce": "On"})
    assert write("Off", {}) == (["option", "muteonce", "vs", "0"], {"muteonce": "Off"})


def test_display_light_write_target():
    desc = next(e for e in airconditioner.DISPLAY_LIGHT.entities if isinstance(e, SwitchDesc))
    assert desc.write_fn is not None
    write = desc.write_fn
    assert write("On", {}) == (["light", "vs", "0"], {"mode": "On"})
    assert write("Off", {}) == (["light", "vs", "0"], {"mode": "Off"})


def test_current_limit_is_read_only():
    """Meaning/write contract for the current-limit levels isn't confirmed
    from the dump alone -- exposed as read-only diagnostic sensors rather
    than a guessed writable control."""
    for desc in airconditioner.CURRENT_LIMIT.entities:
        assert getattr(desc, "write_fn", None) is None


# ---------------------------------------------------------------------------
# TP1X_DA-AC-RAC-01001 cool-only global variant (issue #91). Same modelNum as
# the issue #38 board above, but its /otninformation/vs/0 ships no
# swVersionInfo block, so it is typed purely by its 'RAC' modelNum token --
# which the tokenizer reads out of the hyphenated 'DA-AC-RAC' spelling and the
# underscored 'TP2X_RAC_20K' one alike. Adds /stepcontrol/vs/0 and
# /remotedeviceinfo/vs/0 (both ignored) and exposes the WindFree preset via
# the Nano/NanoSleep convenient-mode codes. Its panel light is carried inside
# /mode/vs/0's options blob instead of a dedicated /light/vs/0 switch.
# ---------------------------------------------------------------------------


def test_tp1x_rac_coolonly_resolves_via_hyphenated_model_token():
    """This unit reports no oneUiVersion at all -- the 'RAC' board token is
    the only thing that types it. Guards the regression where it loaded as
    'unknown'."""
    resources = _load_device("airconditioner_tp1x_rac_coolonly")
    otn = resources.get("/otninformation/vs/0", {})
    assert otn.get("swVersionInfo", {}).get("oneUiVersion", "") == ""
    reg, _ = _resolve("airconditioner_tp1x_rac_coolonly")
    assert reg is not None and reg.name == "airconditioner"


def test_tp1x_rac_coolonly_no_unbound_hrefs():
    """Every resource binds or is ignored -- including the two hrefs unique
    to this dump (/stepcontrol/vs/0, /remotedeviceinfo/vs/0). Clears the gap
    repair."""
    reg, resources = _resolve("airconditioner_tp1x_rac_coolonly")
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_tp1x_rac_coolonly_stray_hrefs_ignored():
    ignored_hrefs = {cap.href for cap in airconditioner.COVERAGE}
    assert "/stepcontrol/vs/0" in ignored_hrefs
    assert "/remotedeviceinfo/vs/0" in ignored_hrefs


def test_tp1x_rac_coolonly_climate_bound():
    reg, resources = _resolve("airconditioner_tp1x_rac_coolonly")
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    climate = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climate) == 1 and climate[0].href == "/mode/vs/0"


def test_tp1x_rac_coolonly_display_light_from_mode_options():
    """This board has no /light/vs/0 switch; the panel light lives in
    /mode/vs/0's options and surfaces as a display_light switch. The token is
    inverted vs its name (confirmed by a live toggle test): with the panel
    lit the option reads `Light_Off`, and with it dark it reads `Light_On`."""
    reg, resources = _resolve("airconditioner_tp1x_rac_coolonly")
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state.get("display_light") is True


def test_display_light_option_parsing_and_gating():
    # Inverted token: Light_Off -> panel lit (on), Light_On -> panel dark (off).
    lit = {"x.com.samsung.da.options": ["CoolCapa_35", "Light_Off", "Volume_Mute"]}
    dark = {"x.com.samsung.da.options": ["Light_On"]}
    absent = {"x.com.samsung.da.options": ["Volume_Mute"]}
    assert airconditioner._display_light_on(lit) is True
    assert airconditioner._display_light_on(dark) is False
    assert airconditioner._display_light_on(absent) is None
    assert airconditioner._has_display_light_option(lit, {}) is True
    assert airconditioner._has_display_light_option(absent, {}) is False


def test_mode_options_display_light_write_is_inverted_single_token():
    """Turning the lamp ON writes the inverted 'Light_Off' token as a
    single-element options list (single-token merge); OFF writes 'Light_On'."""
    sw = next(
        e
        for e in airconditioner.CLIMATE.entities
        if e.key == "display_light" and isinstance(e, SwitchDesc)
    )
    assert sw.write_fn is not None
    assert sw.write_fn("On", {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Light_Off"]},
    )
    assert sw.write_fn("Off", {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Light_On"]},
    )


def test_light_switch_board_gates_out_mode_options_light():
    """Boards with a real /light/vs/0 switch carry no Light_* option, so the
    mode-options display-light entity doesn't double up (mutually exclusive
    encodings)."""
    _reg, resources = _resolve("airconditioner_tp1x_rac")
    assert airconditioner._has_display_light_option(resources["/mode/vs/0"], resources) is False


# ---------------------------------------------------------------------------
# WindFree unit (issue #75): same ARTIK051_PRAC_20K modelNum family as the
# original issue #17 fixture, but its /mode/convenient/vs/0 additionally
# supports Nano/NanoSleep/MotionDirect/MotionIndirect, /wind/direction/vs/0
# additionally supports Left_And_Right, and /humidity/vs/0's
# fivepercentHumidity is populated (unlike the all-zero original dump).
# ---------------------------------------------------------------------------


def _ac_windfree():
    resources = _load_device("airconditioner_windfree")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def test_windfree_no_unbound_hrefs():
    reg, resources = _ac_windfree()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_windfree_humidity_and_temperature_sensors_present():
    reg, resources = _ac_windfree()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["humidity"] == 42.0  # fivepercentHumidity, not the stuck humidity=0 field
    assert state["current_temperature_c"] == 27.0


def test_current_temperature_vs_only_binds_when_ocf_href_absent():
    """CURRENT_TEMPERATURE_VS's match_fn must not double-bind alongside
    CURRENT_TEMPERATURE when a device (like this one) reports both
    /temperature/current/0 and /temperatures/vs/0."""
    match = airconditioner.CURRENT_TEMPERATURE_VS.match_fn
    assert match is not None
    assert match({}, {"/temperature/current/0": {}}) is False
    assert match({}, {}) is True


def test_humidity_reads_five_percent_field_not_stuck_humidity_field():
    desc = airconditioner.HUMIDITY.entities[0]
    assert desc.rep_fn is not None
    rep = {"x.com.samsung.da.humidity": "0", "x.com.samsung.da.fivepercentHumidity": "42"}
    assert desc.rep_fn(rep) == 42.0


def test_humidity_falls_back_to_the_plain_field_where_five_percent_is_absent():
    """ARTIK051 boards (issue #136) have no fivepercentHumidity field at all.
    Their plain field is not stuck -- it carries a reading while Air monitoring
    is on -- so 0 means "not measuring" on both generations, not 0% humidity."""
    desc = airconditioner.HUMIDITY.entities[0]
    assert desc.rep_fn is not None
    assert desc.rep_fn({"x.com.samsung.da.humidity": "51"}) == 51.0
    assert desc.rep_fn({"x.com.samsung.da.humidity": "0"}) is None
    assert desc.rep_fn({}) is None


def test_humidity_five_percent_field_passes_a_genuine_zero_through():
    """issue #160: fivepercentHumidity's zero-as-"not measuring" carve-out
    (added in #146 to cover ARTIK051's plain humidity field) was
    over-applied to fivepercentHumidity too, silently turning a real 0%
    reading on every other AC board into unknown. Only the humidity
    fallback field collapses 0 -- fivepercentHumidity's 0 is a real
    reading."""
    desc = airconditioner.HUMIDITY.entities[0]
    assert desc.rep_fn is not None
    assert desc.rep_fn({"x.com.samsung.da.fivepercentHumidity": "0"}) == 0.0


# ---------------------------------------------------------------------------
# Wind-Free 2-in-1 (TP2X_FAC_BORA_21K, issues #150/#153): a floor-standing +
# wall-mounted indoor unit pair sharing one outdoor unit and one local IP.
# Reported "no climate entity is generated, only power" -- the device simply
# fell back to 'unknown' for lack of a '_FAC_' modelNum routing token; once
# routed, it binds against the exact same CLIMATE composite every other RAC
# family uses, zero unbound hrefs, no new capabilities needed beyond ignoring
# the two hrefs (/subdevices/vs/0, /runn/vs/0) unique to this board.
# ---------------------------------------------------------------------------


def _ac_fac_bora():
    resources = _load_device("airconditioner_fac_bora")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def test_fac_bora_resolves_to_airconditioner_registry():
    reg, _ = _ac_fac_bora()
    assert reg is not None and reg.name == "airconditioner"


def test_fac_bora_no_unbound_hrefs():
    reg, resources = _ac_fac_bora()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_fac_bora_climate_entity_present():
    """The actual reported gap: only a power switch existed before, no
    climate entity at all."""
    reg, resources = _ac_fac_bora()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    assert any(isinstance(item.desc, ClimateDesc) for item in bound)


def test_fac_bora_subdevices_and_runningmode_are_ignored_not_guessed():
    assert "/subdevices/vs/0" in airconditioner._AC_IGNORED
    assert "/runn/vs/0" in airconditioner._AC_IGNORED


# ---------------------------------------------------------------------------
# TP1X_LNX-AC-RAC-01001_0000 -- Lennox-branded heat pump on the Samsung RAC
# board family (issue #173). Routes via the existing '-RAC-' modelNum token,
# same registry as the plain RAC family. Adds two AI-feature resources not
# seen on prior AC dumps: /mds/absencepowersaving/vs/0 (absence-detection
# power saving) and /option/motiondetectwind/stateful/vs/0 (avoid-direct-
# wind-on-motion) -- both exposed read-only, same 'don't guess' precedent as
# CURRENT_LIMIT/ANOMALY_LOAD.
# ---------------------------------------------------------------------------


def _ac_lnx_rac_heatpump():
    resources = _load_device("airconditioner_lnx_rac_heatpump")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"],
        info["x.com.samsung.da.description"],
    )
    return reg, resources


def test_lnx_rac_heatpump_resolves_to_airconditioner_registry():
    reg, _ = _ac_lnx_rac_heatpump()
    assert reg is not None and reg.name == "airconditioner"


def test_lnx_rac_heatpump_no_unbound_hrefs():
    reg, resources = _ac_lnx_rac_heatpump()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_lnx_rac_heatpump_absence_power_saving_state():
    reg, resources = _ac_lnx_rac_heatpump()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["absence_power_saving_active"] is False
    assert state["absence_power_saving_mode"] == "normal"


def test_lnx_rac_heatpump_motion_detect_wind_state():
    reg, resources = _ac_lnx_rac_heatpump()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state["motion_detect_wind_active"] is False
    assert state["motion_detect_wind_mode"] == "indirect"


def test_lnx_rac_heatpump_enable_switches_are_writable():
    """status is a bare On/Off boolean, same shape already shipped writable
    elsewhere in this file (MUTE_ONCE, AUTO_CLEAN) -- worst case a wrong
    token no-ops. The paired mode selects stay read-only sensors."""
    absence_keys = {e.key for e in airconditioner.ABSENCE_POWER_SAVING.entities}
    motion_keys = {e.key for e in airconditioner.MOTION_DETECT_WIND.entities}
    assert absence_keys == {"absence_power_saving_active", "absence_power_saving_mode"}
    assert motion_keys == {"motion_detect_wind_active", "motion_detect_wind_mode"}
    absence_mode = next(
        e
        for e in airconditioner.ABSENCE_POWER_SAVING.entities
        if e.key == "absence_power_saving_mode"
    )
    motion_mode = next(
        e for e in airconditioner.MOTION_DETECT_WIND.entities if e.key == "motion_detect_wind_mode"
    )
    assert not hasattr(absence_mode, "write_fn") or absence_mode.write_fn is None
    assert not hasattr(motion_mode, "write_fn") or motion_mode.write_fn is None


def test_lnx_rac_heatpump_absence_power_saving_write_target():
    desc = next(
        e
        for e in airconditioner.ABSENCE_POWER_SAVING.entities
        if e.key == "absence_power_saving_active" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    write = desc.write_fn
    assert write("On", {}) == (["mds", "absencepowersaving", "vs", "0"], {"status": "On"})
    assert write("Off", {}) == (["mds", "absencepowersaving", "vs", "0"], {"status": "Off"})


def test_lnx_rac_heatpump_motion_detect_wind_write_target():
    desc = next(
        e
        for e in airconditioner.MOTION_DETECT_WIND.entities
        if e.key == "motion_detect_wind_active" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    write = desc.write_fn
    assert write("On", {}) == (
        ["option", "motiondetectwind", "stateful", "vs", "0"],
        {"status": "On"},
    )
    assert write("Off", {}) == (
        ["option", "motiondetectwind", "stateful", "vs", "0"],
        {"status": "Off"},
    )


# ---------------------------------------------------------------------------
# Additive entities layered on the ARTIK051_PRAC family on top of the upstream
# registry: beep (Volume_* option), tropical night mode (Sleep_<N> option),
# filter usage hours + alarm threshold (filterUsage / filterDesiredUsage),
# air-quality sensors (/sensors/vs/0 items), and software/firmware version
# (/information/vs/0 items). Beep and tropical night use the single-token
# option_write merge -- a full options RMW reverts on ARTIK051_PRAC (see the
# [[samsung-ac-local-vs-cloud-control]] memory).
# ---------------------------------------------------------------------------


def _beep_desc():
    return next(e for e in airconditioner.CLIMATE.entities if e.key == "beep")


def _tropical_desc():
    return next(e for e in airconditioner.CLIMATE.entities if e.key == "tropical_night_mode")


def test_beep_read_from_volume_token():
    """Volume_100 (and any non-Mute) -> on; Volume_Mute -> off; no Volume_ slot
    -> None (entity won't bind via exists_fn)."""
    assert airconditioner._beep_on({"x.com.samsung.da.options": ["Volume_100"]}) is True
    assert airconditioner._beep_on({"x.com.samsung.da.options": ["Volume_Mute"]}) is False
    assert airconditioner._beep_on({"x.com.samsung.da.options": ["Light_Off"]}) is None
    assert airconditioner._beep_on({}) is None


def test_beep_write_is_single_token_options_merge():
    """One-element options array, not a full RMW (which reverts on
    ARTIK051_PRAC). 'On' restores the last non-Mute level so an intermediate
    setting (e.g. Volume_50) survives an off/on cycle; falls back to 100 when
    no prior level is known or the prior token is itself Mute."""
    write = _beep_desc().write_fn
    assert write("On", {}) == (["mode", "vs", "0"], {"x.com.samsung.da.options": ["Volume_100"]})
    assert write("On", {"x.com.samsung.da.options": ["Volume_50"]}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Volume_50"]},
    )
    assert write("On", {"x.com.samsung.da.options": ["Volume_Mute"]}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Volume_100"]},
    )
    assert write("Off", {}) == (["mode", "vs", "0"], {"x.com.samsung.da.options": ["Volume_Mute"]})
    assert write("Bogus", {}) is None


def test_beep_absent_when_no_volume_token():
    """TP1X_DA-AC-RAC-01011 carries no Volume_ option -- beep must not bind."""
    reg, resources = _ac_tp1x()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert "beep" not in state


def test_beep_state_on_windfree():
    """The WindFree fixture reports Volume_100 -> beep reads True."""
    reg, resources = _ac_windfree()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["beep"] is True


def test_tropical_night_read_from_sleep_token():
    """Sleep_<N> -> N; absent -> None."""
    assert airconditioner._tropical_night_value({"x.com.samsung.da.options": ["Sleep_0"]}) == 0
    assert airconditioner._tropical_night_value({"x.com.samsung.da.options": ["Sleep_16"]}) == 16
    assert (
        airconditioner._tropical_night_value({"x.com.samsung.da.options": ["Volume_100"]}) is None
    )
    assert airconditioner._tropical_night_value({}) is None


def test_tropical_night_write_is_single_token_options_merge():
    """Valid 0-16 -> `['Sleep_<N>']`; out of range / non-numeric -> None (no
    write). Cloud counterpart: custom.airConditionerTropicalNightMode (0-16)."""
    write = _tropical_desc().write_fn
    assert write(0, {}) == (["mode", "vs", "0"], {"x.com.samsung.da.options": ["Sleep_0"]})
    assert write(16, {}) == (["mode", "vs", "0"], {"x.com.samsung.da.options": ["Sleep_16"]})
    assert write(17, {}) is None
    assert write(-1, {}) is None
    assert write("not-a-number", {}) is None
    # Float rounds to nearest int within range.
    assert write(5.6, {}) == (["mode", "vs", "0"], {"x.com.samsung.da.options": ["Sleep_6"]})


def test_tropical_night_disabled_by_default():
    """Issue #166: Sleep_0 is present in /mode/vs/0's options on every dump
    seen, including the original issue #17 dump this capability was verified
    against (see airconditioner_device.json / _ac()) -- yet the #166 reporter
    confirmed their unit (also an ARTIK051_PRAC_20K board, per its own
    /information/vs/0) has no tropical night mode feature at all. The token
    slot's presence proves nothing about the physical feature, so this can't
    be existence-gated any tighter than it already is -- registered but
    disabled by default instead, same precedent as fridge.rack_count /
    cooktop.paired_hood_model."""
    assert _tropical_desc().enabled_default is False


def test_tropical_night_absent_when_no_sleep_token():
    """TP1X_DA-AC-WAC (window AC) carries no Sleep_ option -- tropical night
    mode must not bind."""
    resources = _load_device("airconditioner_window_ac")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert "tropical_night_mode" not in state


def test_tropical_night_state_levels_across_fixtures():
    """Sleep_0 / Sleep_6 / Sleep_16 surface as 0 / 6 / 16 respectively."""

    def level(name):
        res = _load_device(name)
        info = res["/information/vs/0"]
        r = for_device_by_model(
            info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
        )
        assert r is not None
        return flatten(discover(res, r.capabilities, r.pattern_capabilities), res).get(
            "tropical_night_mode"
        )

    assert level("airconditioner_windfree") == 0
    assert level("airconditioner_tp1x_da_ac_rac_01011") == 6
    assert level("airconditioner_tp2x_rac_20k") == 16


def test_air_filter_usage_hours_reads_raw_count():
    """filterUsage is a lifetime hour counter (41 of 500) that resets on
    filter replacement -- total_increasing, not measurement. Unit comes from
    filterCapacityUnit via unit_fn, not a hardcoded 'h'."""
    desc = next(
        e
        for e in airconditioner.AIR_FILTER.entities
        if e.key == "air_filter_usage_hours" and isinstance(e, SensorDesc)
    )
    assert desc.value_fn("41") == 41
    assert desc.value_fn(41) == 41
    assert desc.value_fn(None) is None
    assert desc.value_fn("not-a-number") is None
    assert desc.device_class == "duration"
    assert desc.state_class == "total_increasing"
    assert desc.unit_fn is not None
    assert desc.unit_fn({"x.com.samsung.da.filterCapacityUnit": "Hour"}) == "h"
    assert desc.unit_fn({"x.com.samsung.da.filterCapacityUnit": "Minute"}) == "min"
    assert desc.unit_fn({}) == "h"  # static fallback when the field is absent


def test_air_filter_threshold_is_writable_select():
    """filterDesiredUsage is a locally writable option (confirmed live on
    ARTIK051_PRAC: POST 700 -> 2.04, persisted). Exposed as a Select keyed to
    the device's supportedFilterDesiredUsage enum; the write POSTs the scalar
    field back to /filter/airdustfilter/vs/0. Only binds where the enum is
    advertised -- boards without it leave this writable field unexposed rather
    than guess the valid set."""
    desc = next(e for e in airconditioner.AIR_FILTER.entities if e.key == "air_filter_threshold")
    assert isinstance(desc, SelectDesc)
    assert desc.options_field == "x.com.samsung.da.supportedFilterDesiredUsage"
    assert desc.exists_fn is not None
    assert (
        desc.exists_fn(
            {"x.com.samsung.da.supportedFilterDesiredUsage": ["180", "300", "500", "700"]}, {}
        )
        is True
    )
    assert desc.exists_fn({}, {}) is False
    # Current value is stringified for option matching.
    assert desc.value_fn("500") == "500"
    assert desc.value_fn(500) == "500"
    assert desc.value_fn(None) is None
    # Write POSTs the selected option as the scalar field.
    assert desc.write_fn is not None
    assert desc.write_fn("700", {}) == (
        ["filter", "airdustfilter", "vs", "0"],
        {"x.com.samsung.da.filterDesiredUsage": "700"},
    )


def test_air_filter_threshold_absent_without_supported_enum():
    """WindFree (ARTIK051_PRAC) advertises no supportedFilterDesiredUsage, so
    the writable threshold Select must not bind there -- even though the
    scalar field is present and writable. Don't expose a control whose valid
    options aren't known."""
    reg, resources = _ac_windfree()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert "air_filter_threshold" not in state
    assert state["air_filter_usage_hours"] == 41
    assert state["air_filter_usage"] == 8  # 41/500 -> 8%


def test_air_filter_threshold_binds_on_enum_board():
    """tp1x_rac advertises supportedFilterDesiredUsage -> threshold Select
    binds, current value read from filterDesiredUsage."""
    reg, resources = _resolve("airconditioner_tp1x_rac")
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["air_filter_threshold"] == "500"


def test_air_quality_sensors_from_sensors_vs_items():
    """/sensors/vs/0 items[] surface as diagnostic scalars (no unit advertised
    on the resource, so no device_class until a populated reading + unit is
    observed -- the 'don't guess' rule). CleanLevel is corroborated as numeric
    by a top-level cleanLevel scalar, so it's an int measurement; the others
    are string diagnostics. Dust/FineDust/SuperFineDust carry a 2-element
    array whose second element is unconfirmed -- v[0] is taken as the reading
    (see _sensor_item_value)."""
    reg, resources = _ac_windfree()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["clean_level"] == 0  # numeric (int), corroborated
    for key in ("odor", "dust", "fine_dust", "super_fine_dust"):
        assert state[key] == "0"  # string diagnostic
    # tp1x_da_ac_rac_01011 is the only fixture with a non-zero air-quality
    # reading -- the one that catches a value_fn regression.
    reg2, resources2 = _ac_tp1x()
    state2 = flatten(discover(resources2, reg2.capabilities, reg2.pattern_capabilities), resources2)
    assert state2["clean_level"] == 1


def test_air_quality_disabled_by_default():
    """Issue #166 (ARxxTXFCAWKNEU, board ARTIK051_PRAC_20K): /sensors/vs/0
    lists all five item types with permanent zero values on both its units --
    the exact same shape as the WindFree/issue #17 dumps this capability was
    first verified against -- yet the reporter confirmed none of these
    sensors are physically present on their model.

    A tighter exists_fn (requiring a corroborating top-level
    x.com.samsung.da.cleanLevel scalar) was tried and reverted: it looked
    like a real signal against this repo's AC fixtures, but
    air_purifier_device.json, air_purifier_vtww_device.json, and
    range_hood_device.json all carry genuinely populated Dust/FineDust/
    SuperFineDust readings with no such scalar, so requiring it would
    silently drop real readings on hardware this repo hasn't seen yet on an
    AC. These stay bound whenever the item type is listed (see
    _has_sensor_type) and disabled by default instead, same precedent as
    fridge.rack_count / cooktop.paired_hood_model / tropical_night_mode --
    units that do have the sensor can enable it themselves."""
    for key in ("clean_level", "odor", "dust", "fine_dust", "super_fine_dust"):
        desc = next(e for e in airconditioner.AIR_QUALITY.entities if e.key == key)
        assert desc.enabled_default is False, key


def test_air_quality_absent_when_no_sensor_items():
    """A board whose /sensors/vs/0 carries an empty items[] (the cool-only
    RAC variant) binds no air-quality entities -- exists_fn gates each on its
    item type, not merely on the href being present."""
    reg, resources = _resolve("airconditioner_tp1x_rac_coolonly")
    assert "/sensors/vs/0" in resources  # the href is there, just empty
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    for key in ("clean_level", "odor", "dust", "fine_dust", "super_fine_dust"):
        assert key not in state, key


def test_sensor_item_value_picks_first_value():
    """_sensor_item_value returns the first element of the value list, as a
    string; None when the item is absent or its value is empty."""
    items = [
        {"x.com.samsung.da.type": "Dust", "x.com.samsung.da.value": ["0", "0"]},
        {"x.com.samsung.da.type": "Odor", "x.com.samsung.da.value": []},
    ]
    assert airconditioner._sensor_item_value(items, "Dust") == "0"
    assert airconditioner._sensor_item_value(items, "Odor") is None
    assert airconditioner._sensor_item_value(items, "Missing") is None
    assert airconditioner._sensor_item_value(None, "Dust") is None


def test_beep_and_tropical_night_stay_off_legacy_krac_board():
    """ARTIK051_KRAC_18K (issue #136) reports both a Volume_ and a Sleep_
    option token. Volume_ is modeled as the shared 'beep' switch (issue #136's
    buzzer_volume Number never correctly modeled this board -- see
    test_legacy_krac_board_beep_is_a_switch_not_a_volume_number below);
    Sleep_ is still good_sleep, not tropical_night_mode, on this board (see
    airconditioner.CLIMATE)."""
    reg, resources = _resolve("airconditioner_artik051_krac_18k")
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert "buzzer_volume" not in state
    assert "tropical_night_mode" not in state
    assert state["beep"] is True
    assert state["good_sleep"] == 0.0


def test_legacy_krac_board_beep_is_a_switch_not_a_volume_number():
    """Issue #136: three real units across two reporters only ever reported
    Volume_100 or Volume_Mute -- never an intermediate value -- and the old
    buzzer_volume Number's write path (a plain integer string) could never
    produce the literal 'Mute' token needed to turn it off. 'beep' now
    applies uniformly across board generations instead of being gated off
    the legacy board."""
    reg, resources = _resolve("airconditioner_artik051_krac_18k")
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["beep"] is True

    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        o.replace("Volume_100", "Volume_Mute")
        for o in resources["/mode/vs/0"]["x.com.samsung.da.options"]
    ]
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["beep"] is False

    path, body = airconditioner._beep_write("Off", resources["/mode/vs/0"])
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["Volume_Mute"]}


def test_legacy_krac_board_energy_kwh_uses_centiwatt_hour_scale():
    """Issue #193: this legacy ARTIK051 board reports cumulativePower in
    centiwatt-hours (raw '117430000'), not the plain Wh common.wh_to_kwh
    assumes -- confirmed against the reporter's own SmartThings-app reading
    of 1,174.30 kWh. ENERGY_METER_LEGACY's /100000 scale must produce that
    exact value, not the /1000-only 117430.0 the generic capability would."""
    reg, resources = _resolve("airconditioner_artik051_krac_energy")
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["energy_kwh"] == 1174.3


def test_non_legacy_board_energy_kwh_still_uses_plain_wh_scale():
    """The ENERGY_METER_GENERIC/LEGACY split must not change behavior for
    every other AC board generation -- same value as plain wh_to_kwh."""
    reg, resources = _ac()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["energy_kwh"] == round(1686632 / 1000.0, 2)


# ---------------------------------------------------------------------------
# TP1X_DA-AC-RAC-01001_0000, reporter's dump. Reported "fan and WindFree are
# missing" -- both already work: /wind/strength/vs/0's 0-4 codes match
# _DEVICE_TO_FAN exactly, and /mode/convenient/vs/0's Nano/NanoSleep codes
# already resolve dynamically via _preset_to_ha + the existing "nano"/
# "nanosleep" -> "WindFree"/"WindFree sleep" translation labels -- confirmed
# below by zero unbound hrefs and the climate entity binding with its usual
# FAN_MODE/PRESET_MODE features. The real, previously-uncaptured gap was
# /mode/vs/0's SmartCoolClean_/ProgressSmartClean_ option tokens (the cloud
# custom.airConditionerOdorController capability) -- see
# _odor_controller_active's docstring.
# ---------------------------------------------------------------------------


def _ac_odor_controller():
    return _resolve("airconditioner_tp1x_rac_odor_controller")


def test_odor_controller_no_unbound_hrefs():
    reg, resources = _ac_odor_controller()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_odor_controller_fan_and_windfree_already_bind():
    """The actually-reported gap wasn't real: fan speed and the WindFree
    preset both come from the composite climate entity, which is bound here
    with its normal fan/preset feature set -- no code change needed for
    either."""
    reg, resources = _ac_odor_controller()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    climate = [b for b in bound if isinstance(b.desc, ClimateDesc)]
    assert len(climate) == 1 and climate[0].href == "/mode/vs/0"
    assert resources["/wind/strength/vs/0"]["x.com.samsung.da.supportedModes"] == [
        "0",
        "1",
        "2",
        "3",
        "4",
    ]
    assert resources["/mode/convenient/vs/0"]["x.com.samsung.da.supportedModes"] == [
        "Off",
        "Sleep",
        "Quiet",
        "Speed",
        "Nano",
        "NanoSleep",
    ]


def test_odor_controller_state_and_progress_present():
    reg, resources = _ac_odor_controller()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert state["odor_controller_active"] is False  # SmartCoolClean_Off
    assert state["odor_controller_progress"] == 0  # ProgressSmartClean_0


def test_odor_controller_read_from_option_tokens():
    assert (
        airconditioner._odor_controller_active({"x.com.samsung.da.options": ["SmartCoolClean_On"]})
        is True
    )
    assert (
        airconditioner._odor_controller_active({"x.com.samsung.da.options": ["SmartCoolClean_Off"]})
        is False
    )
    assert (
        airconditioner._odor_controller_active({"x.com.samsung.da.options": ["Volume_100"]}) is None
    )
    assert (
        airconditioner._odor_controller_progress(
            {"x.com.samsung.da.options": ["ProgressSmartClean_42"]}
        )
        == 42
    )
    assert airconditioner._odor_controller_progress({}) is None


def test_odor_controller_absent_when_no_smartcoolclean_token():
    """The original issue #17 dump's /mode/vs/0 options carry no
    SmartCoolClean_/ProgressSmartClean_ tokens -- neither entity binds."""
    reg, resources = _ac()
    state = flatten(discover(resources, reg.capabilities, reg.pattern_capabilities), resources)
    assert "odor_controller_active" not in state
    assert "odor_controller_progress" not in state


def test_odor_controller_is_read_only():
    """No command capability is confirmed for SmartCoolClean -- exposed
    read-only, same 'don't guess' precedent as CURRENT_LIMIT/ANOMALY_LOAD."""
    active = next(e for e in airconditioner.CLIMATE.entities if e.key == "odor_controller_active")
    progress = next(
        e for e in airconditioner.CLIMATE.entities if e.key == "odor_controller_progress"
    )
    assert not hasattr(active, "write_fn") or active.write_fn is None
    assert not hasattr(progress, "write_fn") or progress.write_fn is None
