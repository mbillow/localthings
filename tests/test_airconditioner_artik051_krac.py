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
    device_serial = "TEST-KRAC-SERIAL"
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
    """Newer families carry Volume/Sleep/OutdoorTemp/Autoclean tokens too,
    while also exposing those settings as dedicated resources -- ungated, the
    token entities would duplicate them (auto clean) or apply a scale
    calibrated on another board generation (outdoor temperature)."""
    state = _state("airconditioner_tp1x_rac")
    for key in (
        "spi",
        "auto_clean_legacy",
        "air_monitoring",
        "good_sleep",
        "outdoor_temperature",
        "filter_time",
    ):
        assert key not in state, key


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


def test_error_message_resolves_the_code_the_way_the_appliance_does():
    """The fixture is a healthy unit -- ErrorCode_OFF / Deleted -- so the codes
    are injected. The strings are Samsung's own, from the catalog its app reads,
    and an unrecognised code has to fall through to the code itself rather than
    to nothing: this table is what one app build knew, not what the appliance can
    report."""
    assert _state()["error_message"] == "none"

    def with_alarm(code, state="Created"):
        fresh = _load_device(FIXTURE)
        fresh["/alarms/vs/0"]["x.com.samsung.da.items"] = [
            {"x.com.samsung.da.code": code, "x.com.samsung.da.state": state}
        ]
        bound, _ = _discover(fresh)
        return flatten(bound, fresh)["error_message"]

    assert with_alarm("ErrorCode_E464") == "IPM Over Current (INV)"
    assert with_alarm("ErrorCode_E554") == "Gas shortage error"
    assert with_alarm("ErrorCode_E999") == "E999"
    assert with_alarm("ErrorCode_E464", state="Deleted") == "none"
    # Reminders are not faults: they have their own entities and no code table.
    assert with_alarm("FilterAlarm") == "none"


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
    """Newer boards run the same cycle off /option/autoclean/vs/0 and say
    nothing about stopping it, so writing a legacy token there would be a
    guess."""
    newer = _load_device("airconditioner_tp1x_rac")
    assert _desc(newer, "auto_clean_stop") is None
    assert "auto_clean_progress_legacy" not in _state("airconditioner_tp1x_rac")


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
    # Codes learned by driving this unit through its cloud integration and
    # reading the token back. They go through the same dynamic resolver as a
    # real convenient resource's supportedModes, so 'Nano' resolves to the
    # existing 'nano' preset -- already labelled WindFree in the catalog.
    assert entity.preset_modes == [
        "none",
        "nano",
        "quiet",
        "comfort",
        "2step",
        "speed",
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
