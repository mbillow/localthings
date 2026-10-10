"""ARTIK051_KRAC_18K room air conditioner (issue #136).

This board generation predates every AC dump the registry was built from and
differs from all of them in three ways, each covered below:

* No ``/wind/*`` resources at all -- fan speed and vane direction share one
  ``/airflow/vs/0`` resource (``speedLevel`` on the same 0-4 scale as
  ``_DEVICE_TO_FAN``, ``direction`` with the same codes as ``_DEVICE_TO_SWING``).
* No ``/mode/convenient/vs/0`` -- the convenient-mode preset is a ``Comode_*``
  token in ``/mode/vs/0``'s ``options`` array.
* Several settings (SPI, auto clean, air monitoring, beep volume, Good Sleep,
  outdoor temperature, filter time and its alarm interval) are ``options``
  tokens too, where newer boards have dedicated resources.

Fan, swing, preset, SPI and beep-volume writes were all confirmed on hardware
by read-back on the unit this fixture is dumped from. The issue #136 unit is
the same model with a slightly different token set (no ``Spi``,
``FilterTime_5460``, ``OutdoorTemp_81``), which the token entities' presence
gating handles the same way it handles newer boards.
"""

from typing import ClassVar, cast

from custom_components.localthings.climate import LocalThingsClimate
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import (
    airconditioner,
    for_device_by_model,
)
from custom_components.localthings.registry.capabilities.airconditioner import (
    HREF_AIRFLOW,
    HREF_WIND_STRENGTH,
    _option_number_write,
    _option_switch_write,
    is_legacy_board,
)
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import ClimateDesc
from tests.conftest import _load_device

FIXTURE = "airconditioner_artik051_krac_18k"
MODEL = "ARTIK051_KRAC_18K|10193441|60010119001111010100000000000000"


class _FakeCoordinator:
    device_key = "TEST-KRAC-SERIAL"
    device_info: ClassVar[dict] = {}
    data: ClassVar[dict] = {}

    def __init__(self, resources):
        self.last_resources = resources
        self.commands = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        # Every bound entity in this test uses the default MAIN
        # subdevice, so the canonical view is just the raw snapshot
        # (issue #177 -- see LocalThingsEntity._resources).
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))

    def learned_modes(self, href):
        return []


def _discover(resources, registry=airconditioner.REGISTRY):
    unbound = []
    bound = discover(
        resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
    )
    return bound, unbound


def _state(fixture=FIXTURE):
    resources = _load_device(fixture)
    bound, _ = _discover(resources)
    return flatten(bound, resources)


def _climate(resources, coordinator=None):
    bound, _ = _discover(resources)
    climate_bound = next(item for item in bound if isinstance(item.desc, ClimateDesc))
    return LocalThingsClimate(
        cast(LocalThingsCoordinator, coordinator or _FakeCoordinator(resources)),
        climate_bound,
    )


# -- device type --------------------------------------------------------------


def test_krac_model_resolves_to_the_airconditioner_registry():
    """The '_RAC_' token check can't see '_KRAC_' -- the 'K' sits between the
    underscore and 'RAC' -- and the consumer-prefix fallback only covers
    washers/dryers/dishwashers, so this model resolved to 'unknown' and
    exposed nothing but power."""
    registry = for_device_by_model(MODEL, "ARTIK051_KRAC_18K")
    assert registry is not None
    assert registry.name == "airconditioner"


def test_no_unbound_hrefs():
    _, unbound = _discover(_load_device(FIXTURE))
    assert unbound == []


# -- option-token entities ----------------------------------------------------


def test_token_entities_present_with_calibrated_values():
    state = _state()
    # token/10 hours: 1715 displayed as "171 hours 0 minutes"... at 1710 in the
    # official app on this unit, which pins the scale (the .5 here is a later
    # reading). It counts up -- see the descriptor comment and
    # test_filter_alarm_tracks_the_counter_against_its_threshold below.
    assert state["filter_time"] == 171.5
    # token - 55 == 19 C, against a 19.4 C forecast at the time of the dump.
    assert state["outdoor_temperature"] == 19.0
    assert state["beep"] is True
    assert state["good_sleep"] == 0.0
    assert state["spi"] is False
    assert state["auto_clean_legacy"] is False
    assert state["air_monitoring"] is False


def test_token_entities_stay_off_newer_boards():
    """Newer families carry Volume/Sleep/Autoclean tokens too, while also
    exposing those settings as dedicated resources -- ungated, the token
    entities would duplicate them."""
    state = _state("airconditioner_tp1x_rac")
    for key in (
        "spi",
        "auto_clean_legacy",
        "air_monitoring",
        "good_sleep",
        "filter_time",
    ):
        assert key not in state, key


def test_outdoor_temperature_is_not_gated_to_legacy_boards():
    """issue #367: OutdoorTemp_ is not paired with any dedicated resource on
    newer boards -- /temperatures/vs/0 carries indoor temperature only, no
    outdoor equivalent exists anywhere in this fixture's 23 hrefs -- so unlike
    auto_clean_legacy et al. above, gating it to is_legacy_board only dropped
    a real reading. A 48h field capture correlated the token against
    weather.forecast_home at r=0.92 across several non-legacy boards,
    confirming it is live per-site data rather than a firmware constant."""
    resources = _load_device("airconditioner_tp1x_rac")
    assert is_legacy_board(resources) is False
    bound, _ = _discover(resources)
    state = flatten(bound, resources)
    assert state["outdoor_temperature"] == 37.0  # OutdoorTemp_92, the fixture's own value


def test_climate_legacy_airflow_gate_agrees_with_is_legacy_board():
    """issue #161: climate.py's _legacy_airflow() delegates to
    capabilities/airconditioner.py's is_legacy_board() instead of
    re-implementing the same presence/absence check, so the token entities
    and the climate card's legacy read/write paths can't drift apart on
    which board generation is in play."""
    legacy_resources = _load_device(FIXTURE)
    assert is_legacy_board(legacy_resources) is True
    assert _climate(legacy_resources)._legacy_airflow() == legacy_resources[HREF_AIRFLOW]

    newer_resources = _load_device("airconditioner_tp1x_rac")
    assert is_legacy_board(newer_resources) is False
    assert _climate(newer_resources)._legacy_airflow() == {}
    assert HREF_WIND_STRENGTH in newer_resources


def test_absent_token_yields_no_entity():
    """The issue #136 unit of this same model reports no Spi token."""
    resources = _load_device(FIXTURE)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        option for option in options if not option.startswith("Spi_")
    ]
    bound, _ = _discover(resources)
    assert "spi" not in flatten(bound, resources)


def test_humidity_reads_the_vendor_field_and_treats_zero_as_unknown():
    """This board has no fivepercentHumidity field; the plain humidity field
    only carries a reading while Air monitoring is on, and the unit switches
    that back off by itself after about a minute."""
    resources = _load_device(FIXTURE)
    assert resources["/humidity/vs/0"]["x.com.samsung.da.humidity"] == "0"
    bound, _ = _discover(resources)
    assert flatten(bound, resources)["humidity"] is None

    resources["/humidity/vs/0"]["x.com.samsung.da.humidity"] = "51"
    bound, _ = _discover(resources)
    assert flatten(bound, resources)["humidity"] == 51.0


def test_option_writes_carry_one_token():
    """Both go through option_write's single-token merge, the same mechanism
    the display light already uses on this href. Confirmed on hardware by
    read-back: Spi_On/Spi_Off, and the volume token surviving a write."""
    assert _option_switch_write("Spi")("On", {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Spi_On"]},
    )
    # The number platform hands over a float; the device wants an integer token.
    assert _option_number_write("Volume")(70.0, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Volume_70"]},
    )


# -- filter counter and its reset ---------------------------------------------


def _desc(resources, key):
    """The bound descriptor for `key`, or None when its exists_fn declines it.

    flatten() gives values, not descriptors, so this is how a test reaches a
    descriptor's own write_fn and options without standing up an HA entity.
    """
    bound, _ = _discover(resources)
    for item in bound:
        if item.desc.key == key and (
            item.desc.exists_fn is None
            or item.desc.exists_fn(resources.get(item.href) or {}, resources)
        ):
            return item.desc
    return None


def test_good_sleep_is_hours_while_the_token_counts_half_hours():
    """The appliance's own app pairs its duration picker with the values it sends
    one to one -- 0:30 -> 1, 1:00 -> 2, 3:00 -> 6, 12:00 -> 24 -- so the token is
    half hours and the entity, which is in hours, has to halve and double. The
    fixture's own Sleep_0 is the one value that reads the same either way, hence
    the injected token here."""
    resources = _load_device(FIXTURE)
    mode = resources["/mode/vs/0"]
    mode["x.com.samsung.da.options"] = [
        option for option in mode["x.com.samsung.da.options"] if option != "Sleep_0"
    ] + ["Sleep_5"]
    bound, _ = _discover(resources)
    assert flatten(bound, resources)["good_sleep"] == 2.5

    desc = _desc(resources, "good_sleep")
    assert (desc.native_max, desc.step) == (12, 0.5)
    assert desc.write_fn(2.5, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Comode_Sleep", "Sleep_5"]},
    )
    # 12 hours is the app's maximum and has to be reachable -- it was not while
    # the token was published as hours.
    assert desc.write_fn(12, {})[1]["x.com.samsung.da.options"] == ["Comode_Sleep", "Sleep_24"]


def _options(rep_options):
    return {"x.com.samsung.da.options": list(rep_options)}


def test_good_sleep_write_carries_the_mode_token_the_duration_belongs_to():
    """`Sleep_<n>` on its own does nothing. Measured on an ARTIK051_KRAC_18K:
    writing `["Sleep_4"]` was answered 2.04 Changed and the token still read
    `Sleep_0` at +8s and +45s, while `["Comode_Sleep", "Sleep_4"]` held. The
    duration is a parameter of the mode, so both go in one write -- which is
    also the only form the appliance's own app sends."""
    desc = _desc(_load_device(FIXTURE), "good_sleep")

    assert desc.write_fn(2, _options(["Comode_Off", "Sleep_0"])) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["Comode_Sleep", "Sleep_4"]},
    )
    # Off means leaving the mode as well as zeroing the duration.
    assert desc.write_fn(0, _options(["Comode_Sleep", "Sleep_4"]))[1] == _options(
        ["Comode_Off", "Sleep_0"]
    )


def test_good_sleep_and_nano_wind_share_one_token():
    """Nano wind and Good Sleep are one Comode_ slot, so running both is
    Comode_NanoSleep -- the board produces that code by itself when nano is
    asked for while the timer runs. Turning the timer off then has to leave nano
    running rather than switching the mode off entirely, which is how the app
    reads it back."""
    desc = _desc(_load_device(FIXTURE), "good_sleep")

    for comode in ("Comode_Nano", "Comode_NanoSleep"):
        assert desc.write_fn(2, _options([comode, "Sleep_0"]))[1] == _options(
            ["Comode_NanoSleep", "Sleep_4"]
        )
    assert desc.write_fn(0, _options(["Comode_NanoSleep", "Sleep_4"]))[1] == _options(
        ["Comode_Nano", "Sleep_0"]
    )


def test_filter_alarm_time_reads_the_threshold_and_writes_one_token():
    """The interval FilterTime_ is measured against, offered by the app as a
    180/300/500/700 hour radio. All four were walked on hardware while watching
    all 19 resources: the token carries the hour count verbatim and each step
    moved that one field and nothing else, which is also what makes a static
    options tuple defensible here (the board advertises no supported-values
    list for options[] tokens)."""
    state = _state()
    assert state["filter_alarm_time"] == "500"  # the fixture's own value

    desc = _desc(_load_device(FIXTURE), "filter_alarm_time")
    assert desc.options == ("180", "300", "500", "700")
    assert desc.write_fn("180", {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["FilterAlarmTime_180"]},
    )


def test_auto_clean_progress_and_stop_come_off_their_own_tokens():
    """Three tokens describe the drying cycle and the switch only covered the
    first. AutocleanProgress_ is a percentage -- the app renders it into a
    `<progress max="100">` beside a "{{value}}%" label -- and StopAutoClean_ is
    the channel for ending a cycle early, whose presence is what says the
    appliance accepts that at all (the fixture reports Idle, this unit's
    resting value)."""
    assert _state()["auto_clean_progress_legacy"] == 1.0  # the fixture's own value

    desc = _desc(_load_device(FIXTURE), "auto_clean_stop")
    assert desc is not None
    assert desc.write_fn(desc.payload, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["StopAutoClean_Set"]},
    )


def test_auto_clean_stop_stays_off_boards_without_the_token():
    """Newer boards run the same cycle off /option/autoclean/vs/0 and stop it
    there (below), so the legacy token write stays off them."""
    newer = _load_device("airconditioner_tp1x_rac")
    assert _desc(newer, "auto_clean_stop") is None
    assert "auto_clean_progress_legacy" not in _state("airconditioner_tp1x_rac")


def test_auto_clean_cycle_stop_writes_status_stop():
    """Writing status Stop to /option/autoclean/vs/0 ended a running cycle on
    an AR70F09C1AWNEU and left settingStatus On (#598)."""
    desc = _desc(_load_device("airconditioner_tp1x_rac_01001"), "auto_clean_cycle_stop")
    assert desc is not None
    assert desc.translation_key == "auto_clean_stop"
    assert desc.write_fn(desc.payload, {}) == (
        ["option", "autoclean", "vs", "0"],
        {"x.com.samsung.da.status": "Stop"},
    )


def test_auto_clean_cycle_stop_needs_stop_in_supported_status():
    resources = _load_device("airconditioner_tp1x_rac_01001")
    autoclean = dict(resources["/option/autoclean/vs/0"])
    autoclean["x.com.samsung.da.supportedStatus"] = ["Start"]
    assert (
        _desc({**resources, "/option/autoclean/vs/0": autoclean}, "auto_clean_cycle_stop") is None
    )


def test_legacy_boards_get_only_the_token_stop():
    """No board on record carries both, so no appliance shows two stop buttons."""
    assert _desc(_load_device(FIXTURE), "auto_clean_cycle_stop") is None


def test_filter_time_reset_writes_the_appliance_s_own_trigger_token():
    """FilterCleanAlarm_Clear, measured on hardware: 2.04 Changed and
    FilterTime_95 -> FilterTime_0, still zero on a fresh session and every poll
    after. The token is a trigger the board acts on rather than a value it
    stores -- it never appears in options[] -- so the button is gated on the
    counter's own token being present, which is what says this board has a
    filter timer at all."""
    desc = _desc(_load_device(FIXTURE), "filter_time_reset")
    assert desc is not None
    assert desc.write_fn(desc.payload, {}) == (
        ["mode", "vs", "0"],
        {"x.com.samsung.da.options": ["FilterCleanAlarm_Clear"]},
    )


def test_filter_time_reset_stays_off_boards_without_the_counter():
    """No FilterTime_ token, no counter to reset. Newer boards report filter
    usage through their own resource and would need a different mechanism, so
    offering a button that writes a legacy token there would be a guess."""
    assert _desc(_load_device("airconditioner_tp1x_rac"), "filter_time_reset") is None


def test_filter_alarm_time_stays_off_boards_with_a_real_threshold_resource():
    """Newer boards carry air_filter_threshold off supportedFilterDesiredUsage;
    two thresholds on one device would be a coin flip for the user.

    No non-legacy fixture carries a FilterAlarmTime_ token today (only the two
    KRAC dumps have one at all), so asserting on an unmodified newer board
    would pass for the wrong reason -- absent token rather than the
    board-generation gate. The token is injected to exercise the gate itself,
    test_absent_token_yields_no_entity's technique in reverse."""
    newer = _load_device("airconditioner_tp1x_rac")
    assert _desc(newer, "filter_alarm_time") is None

    mode = newer["/mode/vs/0"]
    mode["x.com.samsung.da.options"] = [
        *(mode.get("x.com.samsung.da.options") or []),
        "FilterAlarmTime_500",
    ]
    assert is_legacy_board(newer) is False
    assert _desc(newer, "filter_alarm_time") is None


def test_filter_alarm_tracks_the_counter_against_its_threshold():
    """Why filter_time is read as elapsed rather than remaining: the same
    options blob carries the threshold, and /alarms/vs/0's filter entry is a
    'FilterAlarm_OFF'/'Deleted' placeholder below it. The sibling unit on the
    same site, at FilterTime_5595 against the same FilterAlarmTime_500, instead
    reported an unsuffixed 'FilterAlarm' in state 'Created'."""
    resources = _load_device(FIXTURE)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    assert "FilterTime_1715" in options
    assert "FilterAlarmTime_500" in options

    alarms = resources["/alarms/vs/0"]["x.com.samsung.da.items"]
    filter_alarm = next(
        item for item in alarms if item["x.com.samsung.da.code"].startswith("FilterAlarm")
    )
    assert filter_alarm["x.com.samsung.da.code"] == "FilterAlarm_OFF"
    assert filter_alarm["x.com.samsung.da.state"] == "Deleted"


# -- climate entity: fan, swing and preset off /airflow/vs/0 ------------------


def test_fan_mode_reads_the_airflow_speed_level():
    entity = _climate(_load_device(FIXTURE))
    assert entity.fan_mode == "high"  # speedLevel 3 in the fixture
    # No supportedModes on this resource, so the full 0-4 scale is offered.
    assert entity.fan_modes == ["auto", "low", "medium", "high", "turbo"]


def test_swing_mode_reads_the_airflow_direction():
    resources = _load_device(FIXTURE)
    entity = _climate(resources)
    assert entity.swing_mode == "off"  # 'Fix' in the fixture

    resources[HREF_AIRFLOW]["x.com.samsung.da.direction"] = "All"
    assert _climate(resources).swing_mode == "both"
    assert "both" in _climate(resources).swing_modes


async def test_fan_and_swing_writes_target_the_airflow_resource():
    resources = _load_device(FIXTURE)
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    await entity.async_set_fan_mode("turbo")
    await entity.async_set_swing_mode("both")

    assert [payload for _, payload in coordinator.commands] == [
        ("fan_legacy", "4"),
        ("swing_legacy", "All"),
    ]


def test_preset_comes_from_the_comode_token():
    resources = _load_device(FIXTURE)
    entity = _climate(resources)
    assert entity.preset_mode == "none"  # Comode_Off in the fixture
    # The codes go through the same dynamic resolver as a real convenient
    # resource's supportedModes, so 'Nano' resolves to the existing 'nano' preset
    # -- already labelled WindFree in the catalog. Which of them are offered
    # depends on the HVAC mode and on the unit's own capability bits; the fixture
    # is in Cool, and test_presets_follow_the_hvac_mode_and_the_capability_bits
    # covers every mode.
    assert entity.preset_modes == [
        "none",
        "nano",
        "speed",
        "2step",
        "quiet",
        "comfort",
        "smart",
        "sleep",
        "nanosleep",
    ]

    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        "Comode_Nano" if option.startswith("Comode_") else option for option in options
    ]
    assert _climate(resources).preset_mode == "nano"


async def test_preset_write_uses_the_token_path():
    resources = _load_device(FIXTURE)
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    await entity.async_set_preset_mode("nano")

    assert coordinator.commands[-1][1] == ("preset_legacy", "Nano")


def test_the_sleep_modes_are_presets_too():
    """Good Sleep lives in the same Comode_ slot as the presets, so a unit
    running it reports a code that was not in the list -- and a preset_mode
    outside preset_modes is not a state HA allows. Verified against a live unit:
    with the board on Comode_Sleep, the entity reported preset_mode 'sleep'
    while preset_modes offered only none/nano/quiet/comfort/2step/speed."""
    resources = _load_device(FIXTURE)
    assert _climate(resources).preset_modes[-2:] == ["sleep", "nanosleep"]

    for token, preset in (("Comode_Sleep", "sleep"), ("Comode_NanoSleep", "nanosleep")):
        options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
        resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
            token if option.startswith("Comode_") else option for option in options
        ]
        entity = _climate(resources)
        assert entity.preset_mode == preset
        assert preset in entity.preset_modes


def test_boards_without_the_sleep_token_do_not_get_the_sleep_presets():
    """The codes come with the Sleep_ token; a unit that has no such token has
    nothing to report them from."""
    resources = _load_device(FIXTURE)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        option for option in options if not option.startswith("Sleep_")
    ]
    presets = _climate(resources).preset_modes
    assert "sleep" not in presets and "nanosleep" not in presets


def _in_mode(mode, fixture=FIXTURE):
    resources = _load_device(fixture)
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = [mode]
    return _climate(resources).preset_modes


def test_presets_follow_the_hvac_mode_and_the_capability_bits():
    """The fixture's own OptionCode_35882 / ExtendOptionCode_7 say: WindFree yes
    (eoc[31]), 18K yes (eoc[30]), Quiet yes (oc[10]), Turbo/Comfort in Heat yes
    (oc[12]), d'light no (oc[2]), Single User no (oc[3], oc[11]).

    Its owner read the same lists off the remote and the appliance's app --
    2-Step/Fast Turbo/Comfort/Quiet/WindFree in Cool, Fast Turbo/Comfort/Quiet in
    Heat with WindFree impossible, WindFree alone in Dry and Fan, nothing in Auto
    beyond WindFree (which the app reaches by switching to Cool) -- and the
    appliance itself refused Comode_Nano in Auto and accepted it in Cool.
    """
    assert _in_mode("Cool") == [
        "none",
        "nano",
        "speed",
        "2step",
        "quiet",
        "comfort",
        "smart",
        "sleep",
        "nanosleep",
    ]
    # Heat: no WindFree at all, and no Smart Saver (Cool-only).
    heat = _in_mode("Heat")
    assert "nano" not in heat
    assert "smart" not in heat
    assert [c for c in ("speed", "comfort", "quiet") if c in heat] == ["speed", "comfort", "quiet"]
    # Dry and Fan: WindFree is the only one, and it keeps the mode.
    for mode in ("Dry", "Wind"):
        assert _in_mode(mode) == ["none", "nano"]
    # Auto: WindFree only because this is an 18K model.
    assert _in_mode("Auto") == ["none", "nano"]
    # d'light Cool is a live rule and this unit's oc[2] denies it everywhere.
    for mode in ("Cool", "Heat", "Dry", "Wind", "Auto"):
        assert "dlightcool" not in _in_mode(mode), mode


def test_a_board_with_only_the_old_map_keeps_the_unconditional_list():
    """One map is not enough to judge by. `airconditioner_artik051_dongle_fac_18k`
    is a legacy board that publishes OptionCode and no ExtendOptionCode, so every
    eoc-gated rule would read None -- and None means "this board does not publish
    the map", not "the feature is absent". Deriving from it would have cost that
    unit WindFree in every mode, and left it with ['none'] alone in its own
    fixture mode.

    Its OptionCode is also 521, three orders of magnitude below the RAC-class
    values these bit positions were read from, which is the second reason not to
    interpret it: the FAC and CAC families use the field differently.
    """
    resources = _load_device("airconditioner_artik051_dongle_fac_18k")
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    assert any(o.startswith("OptionCode_") for o in options)
    assert not any(o.startswith("ExtendOptionCode_") for o in options)

    baseline = ["none", "nano", "quiet", "comfort", "2step", "speed"]
    for mode in ("Auto", "Cool", "Heat", "Dry", "Wind"):
        resources["/mode/vs/0"]["x.com.samsung.da.modes"] = [mode]
        assert _climate(resources).preset_modes == baseline, mode


def test_the_other_board_with_both_maps_still_derives():
    """`airconditioner_artik051_krac_energy` is the same model as the fixture
    above with a different OptionCode (56378), and carries both maps -- so it
    stays on the derived path rather than the fallback."""
    presets = _in_mode("Cool", fixture="airconditioner_artik051_krac_energy")
    assert presets[:1] == ["none"]
    assert "nano" in presets and "smart" in presets
    assert presets != ["none", "nano", "quiet", "comfort", "2step", "speed"]


def test_an_unknown_hvac_mode_falls_back_instead_of_deriving():
    """An HVAC mode these rules have never seen is the same "cannot judge" case
    as an absent map, so it gets the same answer rather than a derived-but-wrong
    one. Reachable with a partial or malformed rep, where `modes` is missing."""
    resources = _load_device(FIXTURE)
    for modes in ([], ["CoolClean"]):
        resources["/mode/vs/0"]["x.com.samsung.da.modes"] = modes
        assert _climate(resources).preset_modes == [
            "none",
            "nano",
            "quiet",
            "comfort",
            "2step",
            "speed",
        ], modes


async def test_aicomfort_neither_offers_nano_nor_switches_the_mode():
    """The app disables WindFree in AIComfort, so it is not offered -- and the
    Cool-first write is therefore Auto-only, with no unreachable branch for a
    mode that can never ask for it."""
    resources = _load_device(FIXTURE)
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["AIComfort"]
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    assert "nano" not in entity.preset_modes
    await entity.async_set_preset_mode("quiet")
    assert [payload for _, payload in coordinator.commands] == [("preset_legacy", "Quiet")]


def test_a_bit_that_is_zero_removes_its_preset():
    """oc[12] is what puts Fast Turbo and Comfort in Heat; without it the app
    hides both, and so does this."""
    resources = _load_device(FIXTURE)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    # 35882 with oc[12] cleared, everything else untouched.
    assert format(35882, "016b")[12] == "1"
    cleared = int(format(35882, "016b")[:12] + "0" + format(35882, "016b")[13:], 2)
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        f"OptionCode_{cleared}" if option.startswith("OptionCode_") else option
        for option in options
    ]
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["Heat"]
    presets = _climate(resources).preset_modes
    assert "speed" not in presets and "comfort" not in presets
    assert "quiet" in presets  # oc[10], untouched


def test_a_board_without_the_bit_maps_keeps_the_unconditional_list():
    """An absent map is not a claim that nothing is supported -- issue #136's unit
    of this same model publishes a different token set, and a board that omits
    OptionCode entirely must not lose every preset."""
    resources = _load_device(FIXTURE)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        option for option in options if not option.startswith(("OptionCode_", "ExtendOptionCode_"))
    ]
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["Auto"]
    assert _climate(resources).preset_modes == [
        "none",
        "nano",
        "quiet",
        "comfort",
        "2step",
        "speed",
    ]


def test_the_active_code_is_always_listed_even_where_the_rules_deny_it():
    """A remote can put the unit in a mode these rules would not offer -- and a
    preset_mode outside preset_modes is not a state HA allows, so the appliance
    has the last word. Measured: Comode_2Step was accepted and held while the
    unit was in Auto, where neither the remote nor the app offers it."""
    resources = _load_device(FIXTURE)
    options = resources["/mode/vs/0"]["x.com.samsung.da.options"]
    resources["/mode/vs/0"]["x.com.samsung.da.options"] = [
        "Comode_2Step" if option.startswith("Comode_") else option for option in options
    ]
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["Auto"]
    entity = _climate(resources)
    assert entity.preset_mode == "2step"
    assert "2step" in entity.preset_modes


async def test_nano_in_auto_switches_the_board_to_cool_first():
    """Comode_Nano written while the unit is in Auto is answered 2.04 Changed and
    dropped; the same token after a separate mode write holds. Putting modes and
    options in one POST does not work either, so the mode goes first, on its own.
    """
    resources = _load_device(FIXTURE)
    resources["/mode/vs/0"]["x.com.samsung.da.modes"] = ["Auto"]
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    await entity.async_set_preset_mode("nano")

    assert [payload for _, payload in coordinator.commands] == [
        ("mode", "Cool"),
        ("preset_legacy", "Nano"),
    ]


async def test_nano_outside_auto_writes_only_the_preset():
    """In Cool the token holds on its own, so nothing else is sent -- and in Dry
    the mode must be left alone, since WindFree keeps it."""
    for mode in ("Cool", "Dry"):
        resources = _load_device(FIXTURE)
        resources["/mode/vs/0"]["x.com.samsung.da.modes"] = [mode]
        coordinator = _FakeCoordinator(resources)
        await _climate(resources, coordinator).async_set_preset_mode("nano")
        assert [payload for _, payload in coordinator.commands] == [("preset_legacy", "Nano")], mode


def test_nano_preset_keeps_a_running_good_sleep_at_its_own_duration():
    """Writing a bare Comode_Nano over a live Comode_Sleep/Sleep_4 came back as
    Comode_NanoSleep/Sleep_16 -- the board upgrades the code by itself and then
    supplies a duration of its own, turning two hours into eight without anyone
    asking. Sending the pair keeps the user's value."""
    from custom_components.localthings.registry.capabilities.airconditioner import _climate_write

    assert _climate_write(("preset_legacy", "Nano"), _options(["Comode_Sleep", "Sleep_4"]))[
        1
    ] == _options(["Comode_NanoSleep", "Sleep_4"])
    # Idle timer: nano is just nano, exactly as before.
    assert _climate_write(("preset_legacy", "Nano"), _options(["Comode_Off", "Sleep_0"]))[
        1
    ] == _options(["Comode_Nano"])
    # A sleep preset selected outright has no duration to reuse, so it takes the
    # one the appliance itself falls back to (Sleep_16, eight hours).
    assert _climate_write(("preset_legacy", "Sleep"), _options(["Comode_Off", "Sleep_0"]))[
        1
    ] == _options(["Comode_Sleep", "Sleep_16"])
    # Any other preset is untouched by all of this.
    assert _climate_write(("preset_legacy", "Quiet"), _options(["Comode_Sleep", "Sleep_4"]))[
        1
    ] == _options(["Comode_Quiet"])


async def test_newer_boards_keep_the_resource_paths():
    """The legacy fallbacks are gated on this board's resource shape, so a
    board with /wind/* and /mode/convenient/vs/0 must be untouched by them."""
    resources = _load_device("airconditioner_tp1x_rac")
    coordinator = _FakeCoordinator(resources)
    entity = _climate(resources, coordinator)

    await entity.async_set_fan_mode("high")
    await entity.async_set_swing_mode("off")
    await entity.async_set_preset_mode("quiet")  # from its own supportedModes

    kinds = [payload[0] for _, payload in coordinator.commands]
    assert kinds == ["fan", "swing", "preset"]
