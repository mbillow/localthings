"""Unit tests for oven-family capabilities."""

import pytest

from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import (
    NumberDesc,
    SelectDesc,
    SwitchDesc,
)

# ---------------------------------------------------------------------------
# Device-type detection + full-dump coverage (issue #55)
# ---------------------------------------------------------------------------


def test_oven_fixture_resolves_and_has_no_unbound_hrefs():
    """The issue #55 dump previously came back device_type='unknown' with
    /connected/vs/0 unbound -- resolving via the '-OVEN-' modelNum token
    fallback must leave every href in the oven registry bound or ignored."""
    from tests.conftest import _load_device

    resources = _load_device("oven")
    info = resources["/information/vs/0"]
    reg = for_device_by_model(
        info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
    )
    assert reg is not None
    assert reg.name == "oven"

    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


# ---------------------------------------------------------------------------
# OVEN_SETPOINT — NumberDesc with RMW write semantics
# ---------------------------------------------------------------------------


def _oven_setpoint_desc():
    return next(e for e in oven.OVEN_SETPOINT.entities if isinstance(e, NumberDesc))


def test_oven_setpoint_write_is_read_modify_write():
    desc = _oven_setpoint_desc()
    rep = {
        "x.com.samsung.da.items": [
            {"x.com.samsung.da.id": "Target", "x.com.samsung.da.temperature": 0}
        ]
    }
    assert desc.write_fn is not None
    result = desc.write_fn(200, rep)
    assert result is not None
    path, body = result
    assert path[-1] == "0"  # writes back to the resource
    # the produced body preserves the items-array shape the oven expects
    assert "x.com.samsung.da.items" in body or "x.com.samsung.da.temperature" in str(body)


def test_oven_setpoint_rmw_preserves_other_item_fields():
    """RMW must not drop sibling fields (e.g. x.com.samsung.da.current)."""
    desc = _oven_setpoint_desc()
    rep = {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.current": "180",
                "x.com.samsung.da.desired": "180",
            }
        ]
    }
    assert desc.write_fn is not None
    result = desc.write_fn(200, rep)
    assert result is not None
    _path, body = result
    item = body["x.com.samsung.da.items"][0]
    assert item["x.com.samsung.da.desired"] == "200"
    # sibling field preserved
    assert item["x.com.samsung.da.current"] == "180"


def test_oven_setpoint_clamps_to_step():
    """Setpoint must be a multiple of SETPOINT_STEP_C."""
    desc = _oven_setpoint_desc()
    rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.desired": "0"}]}
    assert desc.write_fn is not None
    result = desc.write_fn(202, rep)
    assert result is not None
    _, body = result
    # 202 → nearest 5 = 200
    assert body["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"] == "200"


def test_oven_setpoint_rejects_out_of_range():
    desc = _oven_setpoint_desc()
    rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.desired": "100"}]}
    assert desc.write_fn is not None
    assert desc.write_fn(10, rep) is None  # below min (30)
    assert desc.write_fn(300, rep) is None  # above max (270)


def test_oven_setpoint_rejects_missing_items():
    desc = _oven_setpoint_desc()
    assert desc.write_fn is not None
    assert desc.write_fn(200, {}) is None


# ---------------------------------------------------------------------------
# OVEN_SETPOINT — Fahrenheit bounds (issue #44 range dump reports
# unit="Fahrenheit"; bounds/step must track the live unit, not stay pinned
# to the Celsius defaults)
# ---------------------------------------------------------------------------


def _fahrenheit_rep(desired="0"):
    return {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.desired": desired,
                "x.com.samsung.da.unit": "Fahrenheit",
            }
        ]
    }


def test_oven_setpoint_write_uses_fahrenheit_bounds():
    desc = _oven_setpoint_desc()
    rep = _fahrenheit_rep()
    # 350 is within F bounds (175-550) but above the C max (270) --
    # confirms the write path isn't silently still clamping to Celsius.
    assert desc.write_fn is not None
    result = desc.write_fn(350, rep)
    assert result is not None
    _, body = result
    assert body["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"] == "350"


def test_oven_setpoint_rejects_out_of_range_fahrenheit():
    desc = _oven_setpoint_desc()
    rep = _fahrenheit_rep()
    assert desc.write_fn is not None
    assert desc.write_fn(100, rep) is None  # below F min (175)
    assert desc.write_fn(600, rep) is None  # above F max (550)


def test_oven_setpoint_native_bounds_track_live_unit():
    desc = _oven_setpoint_desc()
    celsius_rep = {"x.com.samsung.da.items": [{"x.com.samsung.da.unit": "Celsius"}]}
    assert desc.native_min_fn is not None
    assert desc.native_max_fn is not None
    assert desc.step_fn is not None
    assert desc.native_min_fn(celsius_rep) == 30.0
    assert desc.native_max_fn(celsius_rep) == 270.0
    fahrenheit_rep = _fahrenheit_rep()
    assert desc.native_min_fn(fahrenheit_rep) == 175.0
    assert desc.native_max_fn(fahrenheit_rep) == 550.0
    assert desc.step_fn(fahrenheit_rep) == 5.0


def test_oven_setpoint_idle_zero_reads_as_unknown():
    """desired='0' is the idle sentinel (NX60T8311SS/AA, #444). Read as
    0 °F it lands the number entity at -18 °C on a metric install."""
    desc = _oven_setpoint_desc()
    assert desc.value_fn(_fahrenheit_rep("0")["x.com.samsung.da.items"]) is None
    assert desc.value_fn(_fahrenheit_rep("350")["x.com.samsung.da.items"]) == 350


# ---------------------------------------------------------------------------
# OVEN_MODE — SelectDesc with non-empty options
# ---------------------------------------------------------------------------


def _oven_mode_desc():
    return next(e for e in oven.OVEN_MODE.entities if isinstance(e, SelectDesc))


def test_oven_mode_options_nonempty():
    desc = _oven_mode_desc()
    assert callable(desc.options)
    assert len(desc.options({})) > 0


def test_oven_mode_options_falls_back_when_no_live_supported_modes():
    desc = _oven_mode_desc()
    assert desc.options({}) == list(oven._OVEN_MODES)


def test_oven_mode_options_reads_live_supported_modes():
    """issue #138: the device's own supportedModes list is used when
    present, instead of the static _OVEN_MODES guess (plus the idle token,
    see the next test)."""
    desc = _oven_mode_desc()
    resources = {
        "/mode/vs/0": {
            "x.com.samsung.da.supportedModes": ["Bake", "AirFryer", "SelfClean"],
        }
    }
    assert desc.options(resources) == ["NoOperation", "Bake", "AirFryer", "SelfClean"]


def test_oven_mode_options_prepends_idle_token_when_live_list_omits_it():
    """Ranges idle in NoOperation but leave it out of supportedModes
    (NX60T8311SS, every range fixture); HA's select renders a current
    option missing from the list as Unknown."""
    desc = _oven_mode_desc()
    resources = {
        "/mode/vs/0": {
            "x.com.samsung.da.supportedModes": ["Bake", "Broil", "KeepWarm"],
            "x.com.samsung.da.modes": ["NoOperation"],
        }
    }
    assert desc.options(resources) == ["NoOperation", "Bake", "Broil", "KeepWarm"]


def test_oven_mode_options_drop_homecare_wizard():
    """LCD_R18 boards list HOMECARE_WIZARD_V2 in supportedModes with no
    modeSpec entry for it (#600); it is neither offered nor written."""
    desc = _oven_mode_desc()
    resources = {
        "/mode/vs/0": {
            "x.com.samsung.da.supportedModes": ["Convection", "HOMECARE_WIZARD_V2"],
        }
    }
    assert desc.options(resources) == ["NoOperation", "Convection"]
    assert desc.write_fn("HOMECARE_WIZARD_V2", resources["/mode/vs/0"]) is None


def _finish_desc():
    return next(e for e in oven.OVEN_OPERATIONAL_STATE.entities if e.key == "finish_time")


class TestOvenFinishTime:
    """A range pushes /operational/state/vs/0 every few seconds during a
    cook (progressPercentage ticks) and its remainingTime seconds sit at
    ':59', so an unrounded now()+remaining produced one logbook entry per
    push and flipped across the minute boundary (NX60T8311SS, 15-minute
    bake). Same fix operational.py already carries: round to the minute,
    hysteresis on the descriptor, and nothing while the cook isn't active."""

    def test_rounded_to_the_minute_and_stable_across_polls(self):
        desc = _finish_desc()
        assert desc.hysteresis is True
        rep = {
            "x.com.samsung.da.state": "Run",
            "x.com.samsung.da.remainingTime": "00:14:59",
        }
        first = desc.rep_fn(rep)
        second = desc.rep_fn(rep)
        assert first.second == 0 and first.microsecond == 0
        assert first == second

    def test_nothing_unless_a_cook_is_running(self):
        desc = _finish_desc()
        # Ready with a leftover remainingTime (boards that don't zero it).
        assert (
            desc.rep_fn(
                {"x.com.samsung.da.state": "Ready", "x.com.samsung.da.remainingTime": "00:01:00"}
            )
            is None
        )
        # Running with no cook time set: the range reports 00:00:00.
        assert (
            desc.rep_fn(
                {"x.com.samsung.da.state": "Run", "x.com.samsung.da.remainingTime": "00:00:00"}
            )
            is None
        )
        assert desc.rep_fn({"x.com.samsung.da.state": "Run"}) is None
        assert (
            desc.rep_fn({"x.com.samsung.da.state": "Run", "x.com.samsung.da.remainingTime": "soon"})
            is None
        )


def test_oven_mode_validate_rejects_idle_token_with_user_facing_key():
    """Listing NoOperation made it selectable (#445 review). Picking it
    must surface a translated error rather than write_fn's silent None."""
    desc = _oven_mode_desc()
    rep = {"x.com.samsung.da.supportedModes": ["Bake", "Broil"]}
    assert desc.validate_fn("NoOperation", rep, {}) == "oven_mode_idle_not_selectable"
    assert oven._oven_mode_write("NoOperation", rep) is None


def test_oven_mode_validate_passes_every_supported_mode():
    desc = _oven_mode_desc()
    rep = {"x.com.samsung.da.supportedModes": ["Bake", "Broil", "KeepWarm"]}
    for mode in rep["x.com.samsung.da.supportedModes"]:
        assert desc.validate_fn(mode, rep, {}) is None
        assert oven._oven_mode_write(mode, rep) is not None


def test_oven_mode_options_does_not_duplicate_idle_token():
    desc = _oven_mode_desc()
    resources = {"/mode/vs/0": {"x.com.samsung.da.supportedModes": ["NoOperation", "Bake"]}}
    assert desc.options(resources) == ["NoOperation", "Bake"]


def test_oven_mode_write_round_trips():
    desc = _oven_mode_desc()
    valid_mode = desc.options({})[1]  # e.g. 'Bake'
    assert desc.write_fn is not None
    result = desc.write_fn(valid_mode, {})
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body["x.com.samsung.da.modes"] == [valid_mode]


def test_oven_mode_rejects_unknown():
    desc = _oven_mode_desc()
    assert desc.write_fn is not None
    assert desc.write_fn("SpaghettiMode", {}) is None


def test_oven_mode_write_validates_against_live_supported_modes():
    """A device reporting its own supportedModes is validated against that
    list, not the static fallback -- issue #138's AirFryer/SelfClean/etc.
    are accepted, and a mode outside the device's own list is rejected even
    if some other model's static guess would have allowed it."""
    desc = _oven_mode_desc()
    rep = {"x.com.samsung.da.supportedModes": ["Bake", "AirFryer", "SelfClean"]}
    assert desc.write_fn is not None
    result = desc.write_fn("AirFryer", rep)
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body["x.com.samsung.da.modes"] == ["AirFryer"]
    assert desc.write_fn("FrozenPizzaPlus", rep) is None


# ---------------------------------------------------------------------------
# OVEN_MODE options-array writes (lamp, sound, fast_preheat, natural_steam).
# Confirmed on real hardware (issue #54): a write only needs to carry the
# changed token -- the device matches by prefix, evicts the stale token, and
# merges the result into the array itself. No read-modify-write needed.
# ---------------------------------------------------------------------------


def _mode_rep(*extra_opts):
    return {
        "x.com.samsung.da.options": [
            "UpperLamp_Off",
            "Sound_On",
            "fastpreheat_Off",
            *extra_opts,
        ]
    }


def test_lamp_write_is_single_token():
    desc = next(e for e in oven.OVEN_MODE.entities if e.key == "lamp" and isinstance(e, SwitchDesc))
    assert desc.write_fn is not None
    result = desc.write_fn("On", _mode_rep())
    assert result is not None
    path, body = result
    assert path == ["mode", "vs", "0"]
    assert body == {"x.com.samsung.da.options": ["UpperLamp_On"]}


def test_lamp_write_requires_existing_options():
    desc = next(e for e in oven.OVEN_MODE.entities if e.key == "lamp" and isinstance(e, SwitchDesc))
    assert desc.write_fn is not None
    assert desc.write_fn("On", {}) is None


def test_sound_write_is_single_token():
    desc = next(
        e for e in oven.OVEN_MODE.entities if e.key == "sound" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    result = desc.write_fn("Off", _mode_rep())
    assert result is not None
    _path, body = result
    assert body == {"x.com.samsung.da.options": ["Sound_Off"]}


def test_natural_steam_write_is_single_token():
    """NaturalSteam's slot may be absent from the live rep until first
    write -- the single-token write covers both the insert and replace case
    identically, since the device merges by prefix either way."""
    desc = next(
        e for e in oven.OVEN_MODE.entities if e.key == "natural_steam" and isinstance(e, SwitchDesc)
    )
    assert desc.write_fn is not None
    result = desc.write_fn("On", _mode_rep())  # no NaturalSteam_* in rep
    assert result is not None
    _path, body = result
    assert body == {"x.com.samsung.da.options": ["NaturalSteam_On"]}


# ---------------------------------------------------------------------------
# OVEN_OPERATIONAL_STATE — cycle_active BinarySensorDesc
# ---------------------------------------------------------------------------


def test_cycle_active_true_when_running():
    desc = next(e for e in oven.OVEN_OPERATIONAL_STATE.entities if e.key == "cycle_active")
    assert desc.value_fn("Run") is True
    assert desc.value_fn("Running") is True


def test_cycle_active_false_when_idle():
    desc = next(e for e in oven.OVEN_OPERATIONAL_STATE.entities if e.key == "cycle_active")
    assert desc.value_fn("Ready") is False
    assert desc.value_fn(None) is False


# ---------------------------------------------------------------------------
# OVEN_OPERATIONAL_STATE — cook_time NumberDesc
# ---------------------------------------------------------------------------


def test_cook_time_write_produces_hms():
    desc = next(
        e
        for e in oven.OVEN_OPERATIONAL_STATE.entities
        if e.key == "cook_time" and isinstance(e, NumberDesc)
    )
    assert desc.write_fn is not None
    result = desc.write_fn(90, {})
    assert result is not None
    path, body = result
    assert path == ["operational", "state", "vs", "0"]
    assert body["x.com.samsung.da.operationTime"] == "01:30:00"
    assert body["x.com.samsung.da.remainingTime"] == "01:30:00"


def test_cook_time_rejects_out_of_range():
    desc = next(
        e
        for e in oven.OVEN_OPERATIONAL_STATE.entities
        if e.key == "cook_time" and isinstance(e, NumberDesc)
    )
    assert desc.write_fn is not None
    assert desc.write_fn(-1, {}) is None
    assert desc.write_fn(1440, {}) is None


def _current_temp_desc():
    return next(e for e in oven.OVEN_SETPOINT.entities if e.key == "current_temp_c")


def _temps(desired, current, unit):
    return {
        "x.com.samsung.da.items": [
            {
                "x.com.samsung.da.desired": desired,
                "x.com.samsung.da.current": current,
                "x.com.samsung.da.unit": unit,
            }
        ]
    }


@pytest.mark.parametrize(
    ("desired", "current", "unit", "expected"),
    [
        ("0", "175", "Fahrenheit", None),  # idle range, floored at Bake's tempMinF (#444)
        ("0", "80", "Celsius", None),  # idle Celsius range, floored at tempMinC
        ("0", "0", "Fahrenheit", None),  # idle wall oven (#300, #277)
        ("0", "0", "Celsius", None),
        ("0", "300", "Fahrenheit", 300),  # cooling down after a cook: real reading
        ("175", "175", "Fahrenheit", 175),  # KeepWarm at exactly the floor
        ("350", "175", "Fahrenheit", 175),  # preheat from cold shows the floor, like the panel
        ("350", "212", "Fahrenheit", 212),
    ],
)
def test_current_temp_blanks_idle_floor_only(desired, current, unit, expected):
    assert _current_temp_desc().rep_fn(_temps(desired, current, unit)) == expected


def test_current_temp_none_without_items():
    assert _current_temp_desc().rep_fn({}) is None
    assert _current_temp_desc().rep_fn({"x.com.samsung.da.items": []}) is None


def _progress_desc():
    return next(e for e in oven.OVEN_OPERATIONAL_STATE.entities if e.key == "progress_percentage")


@pytest.mark.parametrize(
    ("state", "raw", "expected"),
    [
        ("Ready", "1", 0),  # idle floor on every range fixture
        ("Ready", "0", 0),
        ("Run", "1", 1),  # one second into a timed bake (#183)
        ("Run", "57", 57),
        ("Running", "100", 100),
        ("Run", None, None),
    ],
)
def test_progress_percentage_reads_zero_unless_active(state, raw, expected):
    rep = {"x.com.samsung.da.state": state, "x.com.samsung.da.progressPercentage": raw}
    assert _progress_desc().rep_fn(rep) == expected
