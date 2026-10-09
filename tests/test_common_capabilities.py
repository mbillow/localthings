import pytest

from custom_components.localthings.registry.capabilities import (
    air_purifier,
    airconditioner,
    common,
    fridge,
    range_hood,
)
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc, SwitchDesc
from tests.conftest import _load_device


def _reg():
    return {
        c.href: [c]
        for c in (
            common.KIDS_LOCK_GENERIC,
            common.KIDS_LOCK_VS_FALLBACK,
            common.REMOTE_CONTROL_GENERIC,
            common.REMOTE_CONTROL_VS_FALLBACK,
            common.POWER_GENERIC,
            common.POWER_VS_FALLBACK,
            common.ALARMS,
            common.ENERGY_METER,
            common.WATER_METER,
            common.WATER_FILTER,
        )
    }


def test_kids_lock_vs_value_fn():
    """binary_sensor's 'lock' device class is inverted from a plain on/off
    switch: On means open/unlocked (issues #181/#183 -- see
    TestKidsLockFallback.test_vs_fallback_is_read_only for why this is a
    binary_sensor at all)."""
    desc = common.KIDS_LOCK_VS_FALLBACK.entities[0]
    assert desc.value_fn("Lock") is False
    assert desc.value_fn("Ready") is True


def test_common_caps_discover_on_dishwasher(dishwasher_resources):
    bound = discover(dishwasher_resources, _reg())
    keys = {b.desc.key for b in bound}
    assert "child_lock" in keys
    assert "remote_control" in keys
    assert "power_switch" in keys


class TestMergeOptionsField:
    """merge_options_field() is the read side of issue #54's finding: a
    write only needs to carry the changed token, so the coordinator uses
    this to keep its optimistic cache entry complete (every sibling option
    still present) without waiting on a real poll."""

    def test_replaces_matching_prefix(self):
        cached = ["DeviceType_0167", "Course_16", "GMT_04"]
        assert common.merge_options_field(cached, ["Course_1D"]) == [
            "DeviceType_0167",
            "Course_1D",
            "GMT_04",
        ]

    def test_appends_when_prefix_absent(self):
        cached = ["DeviceType_0167"]
        assert common.merge_options_field(cached, ["NaturalSteam_On"]) == [
            "DeviceType_0167",
            "NaturalSteam_On",
        ]

    def test_merges_multiple_tokens_independently(self):
        cached = ["DetergentLevelCtrl_1", "SoftenerLevelCtrl_0", "GMT_04"]
        merged = common.merge_options_field(cached, ["SoftenerLevelCtrl_2"])
        assert merged == ["DetergentLevelCtrl_1", "SoftenerLevelCtrl_2", "GMT_04"]

    def test_keeps_siblings_sharing_a_first_segment(self):
        """washer_wf80h reports MildDetergentAlarm_Off beside
        MildDetergentAlarm_Notice_0; a first-underscore prefix overwrote both."""
        cached = ["MildDetergentAlarm_Off", "MildDetergentAlarm_Notice_0", "Course_1B"]
        assert common.merge_options_field(cached, ["MildDetergentAlarm_On"]) == [
            "MildDetergentAlarm_On",
            "MildDetergentAlarm_Notice_0",
            "Course_1B",
        ]
        assert common.merge_options_field(cached, ["MildDetergentAlarm_Notice_1"]) == [
            "MildDetergentAlarm_Off",
            "MildDetergentAlarm_Notice_1",
            "Course_1B",
        ]

    def test_handles_missing_cache(self):
        assert common.merge_options_field(None, ["Course_1D"]) == ["Course_1D"]

    def test_ignores_malformed_tokens(self):
        cached = ["Course_16"]
        assert common.merge_options_field(cached, ["nounderscore"]) == ["Course_16"]


class TestMergeItemsField:
    """merge_items_field() is the items[]-array counterpart of
    merge_options_field above (issue #91 review feedback): a vendor
    x.com.samsung.da.items[] write only needs to carry the item id plus the
    field(s) being changed, so the coordinator uses this to keep its
    optimistic cache entry complete (current/minimum/maximum/unit still
    present) without waiting on a real poll."""

    def test_merges_fields_into_matching_id(self):
        cached = [
            {
                "x.com.samsung.da.id": "0",
                "x.com.samsung.da.current": "20.0",
                "x.com.samsung.da.maximum": "30",
                "x.com.samsung.da.minimum": "16",
            }
        ]
        merged = common.merge_items_field(
            cached, [{"x.com.samsung.da.id": "0", "x.com.samsung.da.desired": "22"}]
        )
        assert merged == [
            {
                "x.com.samsung.da.id": "0",
                "x.com.samsung.da.current": "20.0",
                "x.com.samsung.da.maximum": "30",
                "x.com.samsung.da.minimum": "16",
                "x.com.samsung.da.desired": "22",
            }
        ]

    def test_appends_when_id_absent(self):
        cached = [{"x.com.samsung.da.id": "0", "x.com.samsung.da.current": "20.0"}]
        merged = common.merge_items_field(
            cached, [{"x.com.samsung.da.id": "1", "x.com.samsung.da.desired": "22"}]
        )
        assert merged == [
            {"x.com.samsung.da.id": "0", "x.com.samsung.da.current": "20.0"},
            {"x.com.samsung.da.id": "1", "x.com.samsung.da.desired": "22"},
        ]

    def test_handles_missing_cache(self):
        assert common.merge_items_field(
            None, [{"x.com.samsung.da.id": "0", "x.com.samsung.da.desired": "22"}]
        ) == [{"x.com.samsung.da.id": "0", "x.com.samsung.da.desired": "22"}]

    def test_ignores_malformed_new_items(self):
        cached = [{"x.com.samsung.da.id": "0", "x.com.samsung.da.current": "20.0"}]
        assert common.merge_items_field(cached, ["not-a-dict"]) == cached


# ---------------------------------------------------------------------------
# OCF-native / vendor '-vs' fallback pairs (power, kids-lock, remote control).
# ---------------------------------------------------------------------------


class TestPowerFallback:
    def test_generic_href_read_write(self):
        assert common.POWER_GENERIC.href == "/power/0"
        desc = next(e for e in common.POWER_GENERIC.entities if isinstance(e, SwitchDesc))
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False
        assert desc.write_fn is not None
        result = desc.write_fn("On", {})
        assert result is not None
        path, body = result
        assert path == ["power", "0"]
        assert body == {"value": True}
        off_result = desc.write_fn("Off", {})
        assert off_result is not None
        assert off_result[1] == {"value": False}

    def test_vs_fallback_binds_only_when_generic_absent(self):
        assert common.POWER_VS_FALLBACK.href == "/power/vs/0"
        assert common.POWER_VS_FALLBACK.match_fn is not None
        assert common.POWER_VS_FALLBACK.match_fn({}, {"/power/vs/0": {}}) is True
        assert common.POWER_VS_FALLBACK.match_fn({}, {"/power/0": {}, "/power/vs/0": {}}) is False

    def test_vs_fallback_read_write(self):
        desc = next(e for e in common.POWER_VS_FALLBACK.entities if isinstance(e, SwitchDesc))
        assert desc.value_fn("On") is True
        assert desc.value_fn("Off") is False
        assert desc.write_fn is not None
        result = desc.write_fn("On", {})
        assert result is not None
        path, body = result
        assert path == ["power", "vs", "0"]
        assert body == {"x.com.samsung.da.power": "On"}

    def test_power_switch_hidden_when_firmware_disallows(self):
        switch = next(e for e in common.POWER_GENERIC.entities if isinstance(e, SwitchDesc))
        sensor = next(e for e in common.POWER_GENERIC.entities if isinstance(e, BinarySensorDesc))
        resources = {
            "/power/0": {"value": True},
            "/wm/setinfo/vs/0": {"x.com.samsung.da.isModelSettingPowerOnOff": "false"},
        }
        assert switch.exists_fn is not None
        assert sensor.exists_fn is not None
        assert switch.exists_fn(resources["/power/0"], resources) is False
        assert sensor.exists_fn(resources["/power/0"], resources) is True

    def test_power_switch_writable_when_firmware_allows(self):
        switch = next(e for e in common.POWER_GENERIC.entities if isinstance(e, SwitchDesc))
        sensor = next(e for e in common.POWER_GENERIC.entities if isinstance(e, BinarySensorDesc))
        resources = {
            "/power/0": {"value": True},
            "/wm/setinfo/vs/0": {"x.com.samsung.da.isModelSettingPowerOnOff": "true"},
        }
        assert switch.exists_fn is not None
        assert sensor.exists_fn is not None
        assert switch.exists_fn(resources["/power/0"], resources) is True
        assert sensor.exists_fn(resources["/power/0"], resources) is False

    def test_power_switch_default_writable_without_setinfo(self):
        switch = next(e for e in common.POWER_GENERIC.entities if isinstance(e, SwitchDesc))
        sensor = next(e for e in common.POWER_GENERIC.entities if isinstance(e, BinarySensorDesc))
        resources = {"/power/0": {"value": True}}
        assert switch.exists_fn is not None
        assert sensor.exists_fn is not None
        assert switch.exists_fn(resources["/power/0"], resources) is True
        assert sensor.exists_fn(resources["/power/0"], resources) is False

    def test_polled_warm_so_power_state_reflects_quickly(self):
        """issue #56 follow-up: neither href had a poll_tier, so power state
        only ever refreshed on the once-per-30s summary poll instead of the
        subscribe/subpoll cadence 'warm'/'hot' hrefs get -- same reasoning
        as REMOTE_CONTROL_GENERIC/VS_FALLBACK's own 'warm' tier."""
        assert common.POWER_GENERIC.poll_tier == "warm"
        assert common.POWER_VS_FALLBACK.poll_tier == "warm"


class TestWmSetinfoFlags:
    def test_washer_fixture_carries_setinfo_from_device0(self):
        """/wm/setinfo/vs/0 lands in the seed snapshot -- no dedicated capability."""
        resources = _load_device("washer")
        assert "/wm/setinfo/vs/0" in resources
        assert common.model_allows_power_on_off(resources) is False
        assert common.model_setting_without_sc(resources) is True

    def test_dishwasher_allows_power_on_off(self):
        resources = _load_device("dishwasher")
        assert common.model_allows_power_on_off(resources) is True
        assert common.model_setting_without_sc(resources) is False

    def test_deleted_alarms_filtered(self):
        assert (
            common._active_alarm_codes(
                [
                    {
                        "x.com.samsung.da.code": "ErrorCode",
                        "x.com.samsung.da.state": "Deleted",
                    },
                ]
            )
            == "none"
        )
        assert (
            common._active_alarm_codes(
                [
                    {
                        "x.com.samsung.da.code": "LE",
                        "x.com.samsung.da.state": "Triggered",
                    },
                    {
                        "x.com.samsung.da.code": "ErrorCode",
                        "x.com.samsung.da.state": "Deleted",
                    },
                ]
            )
            == "LE"
        )

    def test_off_suffixed_placeholder_codes_filtered(self):
        """issue #166: Samsung pre-populates /alarms/vs/0 with one row per
        supported alarm *type*, each carrying a '<Name>_OFF' placeholder
        code (no 'Deleted' state at all) when that alarm isn't firing --
        confirmed not just for 'ErrorCode_OFF' but also 'FilterAlarm_OFF'
        on the same dump. These were previously shown as if they were
        active alarms."""
        assert (
            common._active_alarm_codes(
                [
                    {
                        "x.com.samsung.da.code": "ErrorCode_OFF",
                        "x.com.samsung.da.triggeredTime": "2026-07-28T12:59:22",
                    },
                    {
                        "x.com.samsung.da.code": "FilterAlarm_OFF",
                        "x.com.samsung.da.triggeredTime": "2026-07-28T12:59:22",
                    },
                ]
            )
            == "none"
        )

    def test_real_filter_alarm_not_off_suffixed_still_shown(self):
        """issue #166's actual live filter alert: code 'FilterAlarm' (no
        '_OFF' suffix), state 'Created' -- distinct from the 'FilterAlarm_OFF'
        placeholder and must still surface."""
        assert (
            common._active_alarm_codes(
                [
                    {
                        "x.com.samsung.da.code": "ErrorCode_OFF",
                        "x.com.samsung.da.triggeredTime": "2026-07-28T12:59:22",
                    },
                    {
                        "x.com.samsung.da.code": "FilterAlarm",
                        "x.com.samsung.da.state": "Created",
                        "x.com.samsung.da.triggeredTime": "2026-07-28T12:59:22",
                    },
                ]
            )
            == "FilterAlarm"
        )


class TestKidsLockFallback:
    def test_generic_is_read_only(self):
        """Issues #181/#183: the kids-lock resource is read-only on real
        hardware (writing the *correct* value still 4.05s, and no
        SwitchDesc device_class='lock' is honored by HA -- the switch
        platform only accepts 'outlet'/'switch'). KIDS_LOCK_GENERIC and
        KIDS_LOCK_VS_FALLBACK now share the same shape (BinarySensorDesc,
        device_class='lock', same key) so both surfaces render with the
        same polarity: 'On' means open/unlocked."""
        assert common.KIDS_LOCK_GENERIC.href == "/kidslock/0"
        desc = common.KIDS_LOCK_GENERIC.entities[0]
        assert isinstance(desc, BinarySensorDesc)
        assert desc.value_fn(False) is True  # value=False -> On=Unlocked
        assert desc.value_fn(True) is False  # value=True -> Off=Locked

    def test_vs_fallback_gated(self):
        assert common.KIDS_LOCK_VS_FALLBACK.match_fn is not None
        assert common.KIDS_LOCK_VS_FALLBACK.match_fn({}, {"/kidslock/vs/0": {}}) is True
        assert (
            common.KIDS_LOCK_VS_FALLBACK.match_fn({}, {"/kidslock/0": {}, "/kidslock/vs/0": {}})
            is False
        )

    def test_vs_fallback_is_read_only(self):
        """Issues #181/#183: writing this resource 4.05s on real hardware
        even with the *correct* value ('Run'), and no dump in the fixture
        corpus has ever reported the value this used to write ('Enable')
        back -- every one reports 'Ready' or 'Run'. Never a confirmed write
        contract, so it's a binary_sensor (no write_fn field at all), not
        a switch."""
        desc = common.KIDS_LOCK_VS_FALLBACK.entities[0]
        assert isinstance(desc, BinarySensorDesc)


class TestRemoteControlFallback:
    def test_generic_read(self):
        assert common.REMOTE_CONTROL_GENERIC.href == "/remotectrl/0"
        desc = common.REMOTE_CONTROL_GENERIC.entities[0]
        assert desc.value_fn(True) is True
        assert desc.value_fn(False) is False

    def test_vs_fallback_gated(self):
        assert common.REMOTE_CONTROL_VS_FALLBACK.match_fn is not None
        assert common.REMOTE_CONTROL_VS_FALLBACK.match_fn({}, {"/remotectrl/vs/0": {}}) is True
        assert (
            common.REMOTE_CONTROL_VS_FALLBACK.match_fn(
                {}, {"/remotectrl/0": {}, "/remotectrl/vs/0": {}}
            )
            is False
        )

    def test_polled_warm_so_write_gating_stays_fresh(self):
        """coordinator.async_send_command blocks writes on this signal, so
        it can't sit in the default 'cold' tier (refreshed only once per
        30s summary poll) -- it needs the subscribe/subpoll cadence 'warm'
        and 'hot' hrefs get instead."""
        assert common.REMOTE_CONTROL_GENERIC.poll_tier == "warm"
        assert common.REMOTE_CONTROL_VS_FALLBACK.poll_tier == "warm"


# ---------------------------------------------------------------------------
# Energy meter. instantaneousPower clamps negatives to 0, but the constant
# '-500' sentinel (a dead field on DA_WM_ laundry + dishwasher dumps, issue #6)
# gates power_watts out entirely so it doesn't read as a real idle "0 W".
# ---------------------------------------------------------------------------


class TestEnergyMeter:
    def test_href(self):
        assert common.ENERGY_METER.href == "/energy/consumption/vs/0"

    def test_power_clamps_negative(self):
        pw = next(e for e in common.ENERGY_METER.entities if e.key == "power_watts")
        assert pw.value_fn(-500.0) == 0.0
        assert pw.value_fn(93.0) == 93.0

    def test_power_watts_hidden_for_dead_sentinel(self):
        pw = next(e for e in common.ENERGY_METER.entities if e.key == "power_watts")
        assert pw.exists_fn is not None
        assert pw.exists_fn({"x.com.samsung.da.instantaneousPower": "-500"}, {}) is False

    def test_power_watts_shown_for_real_value(self):
        pw = next(e for e in common.ENERGY_METER.entities if e.key == "power_watts")
        assert pw.exists_fn is not None
        assert pw.exists_fn({"x.com.samsung.da.instantaneousPower": "150"}, {}) is True

    def test_energy_kwh_hidden_when_cumulative_power_absent(self):
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == "energy_kwh")
        assert kwh.exists_fn is not None
        assert kwh.exists_fn({"x.com.samsung.da.instantaneousPower": "-500"}, {}) is False

    def test_energy_kwh_shown_when_present(self):
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == "energy_kwh")
        assert kwh.exists_fn is not None
        assert kwh.exists_fn({"x.com.samsung.da.cumulativePower": "58900"}, {}) is True

    def test_both_entities_included_on_true_stub(self):
        """A true stub -- /device/0's {"href": "..."} "not fetched yet"
        marker (see registry.batch.is_stub_rep) -- means the resource exists
        but data isn't fetched yet; include both so sub-polls populate them."""
        pw = next(e for e in common.ENERGY_METER.entities if e.key == "power_watts")
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == "energy_kwh")
        assert pw.exists_fn is not None
        assert kwh.exists_fn is not None
        stub = {"href": "/energy/consumption/vs/0"}
        assert pw.exists_fn(stub, {}) is True
        assert kwh.exists_fn(stub, {}) is True

    def test_both_entities_hidden_on_genuinely_empty_rep(self):
        """A real {} rep (no 'href' key) is the device's confirmed -- if
        empty -- answer, not a stub, so a model that never populates this
        resource doesn't get a phantom always-"unknown" entity (issue #127)."""
        pw = next(e for e in common.ENERGY_METER.entities if e.key == "power_watts")
        kwh = next(e for e in common.ENERGY_METER.entities if e.key == "energy_kwh")
        assert pw.exists_fn is not None
        assert kwh.exists_fn is not None
        assert pw.exists_fn({}, {}) is False
        assert kwh.exists_fn({}, {}) is False

    def test_power_watts_hidden_when_field_absent_in_populated_rep(self):
        """A populated rep that lacks instantaneousPower must not spawn a
        phantom power sensor (the exists_fn replaces the field-presence gate)."""
        pw = next(e for e in common.ENERGY_METER.entities if e.key == "power_watts")
        assert pw.exists_fn is not None
        assert pw.exists_fn({"x.com.samsung.da.cumulativePower": "5"}, {}) is False


# ---------------------------------------------------------------------------
# has_sensor_type. Presence gate for /sensors/vs/0 items[] types, shared by
# airconditioner.AIR_QUALITY and air_purifier.AIR_QUALITY. The stub carve-out
# is the same contract ENERGY_METER documents for issue #127.
# ---------------------------------------------------------------------------


class TestHasSensorType:
    def test_true_when_type_is_listed(self):
        fn = common.has_sensor_type("CO2")
        assert fn({"x.com.samsung.da.items": [{"x.com.samsung.da.type": "CO2"}]}, {}) is True

    def test_false_when_type_is_absent(self):
        fn = common.has_sensor_type("CO2")
        assert fn({"x.com.samsung.da.items": [{"x.com.samsung.da.type": "Dust"}]}, {}) is False
        assert fn({"x.com.samsung.da.items": []}, {}) is False
        assert fn({}, {}) is False

    def test_true_on_stub_rep(self):
        """A true stub -- /device/0's {"href": "..."} "not fetched yet"
        marker -- must keep the entity so sub-polls can populate it."""
        fn = common.has_sensor_type("CO2")
        assert fn({"href": "/sensors/vs/0"}, {}) is True


# ---------------------------------------------------------------------------
# AI energy-saving level. '0' is off; supportedAiLevel lists the additional
# level(s) on offer. A single-entry list (issue #21 fridge, issue #40 washer)
# is really a binary toggle, so it's exposed as a switch instead of a
# one-choice select; multiple entries get a select with '0' synthesized back
# in as the explicit off option (supportedAiLevel never lists '0' itself, but
# it's a real, observed value of aiLevel).
# ---------------------------------------------------------------------------


def _ai_energy_level_desc(cls_name):
    return next(e for e in common.AI_ENERGY_LEVEL.entities if e.__class__.__name__ == cls_name)


class TestAiEnergyLevelSwitch:
    def _desc(self):
        return _ai_energy_level_desc("SwitchDesc")

    def test_href(self):
        assert common.AI_ENERGY_LEVEL.href == "/energy/ailevel/vs/0"

    def test_shown_only_with_single_supported_level(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "1", "supportedAiLevel": ["1"]}, {}) is True
        assert desc.exists_fn({"aiLevel": "1", "supportedAiLevel": ["1", "2"]}, {}) is False

    def test_hidden_when_supported_level_is_non_list_scalar(self):
        """A stray scalar (e.g. a string) must not be len()-checked as if it
        were a list -- a 2-char string would otherwise wrongly pass `== 1`
        style checks."""
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "1", "supportedAiLevel": "1"}, {}) is False

    def test_hidden_when_supported_level_missing(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "1"}, {}) is False

    def test_hidden_when_supported_level_empty_list(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "0", "supportedAiLevel": []}, {}) is False

    def test_hidden_on_empty_stub_rep(self):
        desc = self._desc()
        assert desc.exists_fn({}, {}) is False

    def test_value_fn(self):
        desc = self._desc()
        assert desc.value_fn("0") is False
        assert desc.value_fn("1") is True

    def test_write_on_uses_the_single_supported_level(self):
        """The on-value is whatever the device calls its one level, not a
        hardcoded '1'."""
        desc = self._desc()
        path, body = desc.write_fn("On", {"supportedAiLevel": ["2"]})
        assert path == ["energy", "ailevel", "vs", "0"]
        assert body == {"aiLevel": "2"}

    def test_write_off(self):
        desc = self._desc()
        _path, body = desc.write_fn("Off", {"supportedAiLevel": ["1"]})
        assert body == {"aiLevel": "0"}


class TestAiEnergyLevelSelect:
    def _desc(self):
        return _ai_energy_level_desc("SelectDesc")

    def test_shown_only_with_multiple_supported_levels(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "1", "supportedAiLevel": ["1", "2"]}, {}) is True
        assert desc.exists_fn({"aiLevel": "1", "supportedAiLevel": ["1"]}, {}) is False

    def test_hidden_when_supported_level_is_non_list_scalar(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "1", "supportedAiLevel": "12"}, {}) is False

    def test_hidden_when_supported_level_missing(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "1"}, {}) is False

    def test_hidden_when_supported_level_empty_list(self):
        desc = self._desc()
        assert desc.exists_fn({"aiLevel": "0", "supportedAiLevel": []}, {}) is False

    def test_no_translation_key(self):
        """aiLevel's values are plain digits that render fine untranslated
        (select.py's _display()) -- no catalog entry to maintain
        against an unknown number of future levels."""
        desc = self._desc()
        assert desc.translation_key is None

    def test_options_synthesize_off(self):
        """'0' is never in supportedAiLevel but is a real, observed aiLevel
        value -- synthesized back in as the explicit off option."""
        desc = self._desc()
        resources = {"/energy/ailevel/vs/0": {"supportedAiLevel": ["1", "2"]}}
        assert desc.options(resources) == ["0", "1", "2"]

    def test_options_empty_when_resource_missing(self):
        desc = self._desc()
        assert desc.options({}) == ["0"]

    def test_write(self):
        desc = self._desc()
        path, body = desc.write_fn("2", {})
        assert path == ["energy", "ailevel", "vs", "0"]
        assert body == {"aiLevel": "2"}


class TestAiEnergyLevelStubDoesNotDecideThePlatform:
    """Issue found in review: entity *creation* runs once, against whatever
    snapshot is current when platforms are set up (see
    __init__.py's async_config_entry_first_refresh-before-forward-entry-setups
    ordering), while flatten() re-evaluates exists_fn every poll against live
    data. Both descriptors share key='ai_energy_level' (see adapter._key), so
    if a stub carve-out let one of them win at setup time while the other
    wins once real data lands, flatten() would feed the already-instantiated
    entity a value shaped for the other platform (e.g. a bool into a Select
    expecting a string option). Neither side gets an is_stub_rep carve-out,
    so an unfetched stub can never win entity creation for either platform --
    the entity simply doesn't appear until a reload happens with real data,
    same as any other exists_fn-gated entity in this codebase that's unlucky
    on first-poll timing, instead of appearing as the wrong widget type."""

    def test_neither_widget_exists_on_true_stub_rep(self):
        switch = _ai_energy_level_desc("SwitchDesc")
        select = _ai_energy_level_desc("SelectDesc")
        stub = {"href": "/energy/ailevel/vs/0"}
        assert switch.exists_fn(stub, {}) is False
        assert select.exists_fn(stub, {}) is False

    def test_neither_widget_exists_on_genuinely_empty_rep(self):
        switch = _ai_energy_level_desc("SwitchDesc")
        select = _ai_energy_level_desc("SelectDesc")
        assert switch.exists_fn({}, {}) is False
        assert select.exists_fn({}, {}) is False


class TestSelfCheckError:
    """Self-check diagnostic error list -- surfaced on hardware that reports
    x.com.samsung.da.error, joined into a single display string."""

    def _desc(self):
        return next(e for e in common.SELF_CHECK.entities if e.key == "selfcheck_error")

    def test_exists_when_field_present(self):
        desc = self._desc()
        assert desc.exists_fn({"x.com.samsung.da.error": ["DA_ERROR_NONE"]}, {}) is True

    def test_does_not_exist_when_field_absent(self):
        desc = self._desc()
        assert desc.exists_fn({"x.com.samsung.da.status": "Ready"}, {}) is False

    def test_exists_for_true_stub_rep(self):
        """A true stub ({"href": "..."}) is /device/0's not-yet-fetched
        marker -- must be included-for-now, same as ENERGY_METER's fields."""
        desc = self._desc()
        assert desc.exists_fn({"href": "/selfcheck/vs/0"}, {}) is True

    def test_hidden_for_genuinely_empty_rep(self):
        """A real {} rep is the device's confirmed empty answer, not a stub --
        must NOT be force-included (issue #127's phantom-entity pattern)."""
        desc = self._desc()
        assert desc.exists_fn({}, {}) is False

    def test_value_joins_list(self):
        desc = self._desc()
        assert desc.value_fn(["E1", "E2"]) == "E1, E2"

    def test_value_passes_through_scalar(self):
        desc = self._desc()
        assert desc.value_fn("DA_ERROR_NONE") == "DA_ERROR_NONE"

    def test_value_none_for_empty_list(self):
        """An empty error list means no value to show -- None (unknown),
        not an empty string."""
        desc = self._desc()
        assert desc.value_fn([]) is None


# ---------------------------------------------------------------------------
# Cross-family bundles (UNIVERSAL / POWER) -- unpacked into every by_type
# registry's _build([...]) call in place of the hand-duplicated capability
# lists that used to live there.
# ---------------------------------------------------------------------------


class TestUniversalAndPowerBundles:
    def test_universal_contains_the_no_conflict_capabilities(self):
        assert set(common.UNIVERSAL) == {
            common.ALARMS,
            common.ENERGY_METER,
            common.FIRMWARE_UPDATE,
            common.SELF_CHECK,
            common.AI_ENERGY_LEVEL,
            common.KIDS_LOCK_GENERIC,
            common.KIDS_LOCK_VS_FALLBACK,
            common.REMOTE_CONTROL_GENERIC,
            common.REMOTE_CONTROL_VS_FALLBACK,
            # Coverage-only, and in UNIVERSAL because the file surface is
            # advertised by every board family on record (issue #301).
            common.FILE_LIST,
            common.FILE_TRANSFER,
        }

    def test_power_kept_separate_for_airconditioners_sake(self):
        """See common.POWER's own comment for why airconditioner opts out."""
        assert set(common.POWER) == {common.POWER_GENERIC, common.POWER_VS_FALLBACK}

    def test_no_overlap_between_the_two_bundles(self):
        assert not (set(common.UNIVERSAL) & set(common.POWER))

    def test_airconditioner_registry_does_not_include_power(self):
        from custom_components.localthings.registry.by_type import airconditioner

        bound_caps = {c for caps in airconditioner.REGISTRY.capabilities.values() for c in caps}
        assert common.POWER_GENERIC not in bound_caps
        assert common.POWER_VS_FALLBACK not in bound_caps


def _water_filter_desc(fixture, key):
    """The bound descriptor for `key`, or None when its exists_fn declines it.

    Same shape as the per-family helpers: flatten() yields values, not
    descriptors, so this is how a test reaches write_fn without standing up
    an HA entity.
    """
    resources = _load_device(fixture)
    for item in discover(resources, _reg()):
        if item.desc.key == key and (
            item.desc.exists_fn is None
            or item.desc.exists_fn(resources.get(item.href) or {}, resources)
        ):
            return item.desc
    return None


def test_water_filter_reset_writes_the_boards_trigger_value():
    """filterReset 'On', measured on a TP1X_REF_21K: filterUsage 100 -> 0 and
    filterStatus replace -> normal, still zero on a fresh DTLS session. The
    value is case-sensitive ('on'/'ON' fault with 5.00) and the field is a
    trigger the board never reports back, so it can't be gated on itself --
    see docs/investigations/filter-reset.md."""
    desc = _water_filter_desc("refrigerator_tp1x_ref_21k_us", "filter_reset")
    assert desc is not None
    assert desc.write_fn(desc.payload, {}) == (
        ["filter", "waterfilter", "vs", "0"],
        {"x.com.samsung.da.filterReset": "On"},
    )


def _reset_gate(rep):
    """The filter_reset button's own exists_fn, against a synthetic rep.

    Fixture-driven here would be vacuous: no fixture pairs a populated
    waterfilter rep with a missing filterResetType (the dishwashers are
    filterStatus 'notused', which WATER_FILTER.match_fn rejects outright,
    or an unfetched stub), so the gate would never actually be called.
    """
    desc = next(e for e in common.WATER_FILTER.entities if e.key == "filter_reset")
    assert desc.exists_fn is not None, "filter_reset must stay gated"
    return desc.exists_fn(rep, {})


def test_water_filter_reset_needs_a_reset_the_device_says_it_supports():
    """filterResetType names which resets exist, and the corpus carries
    ['notresetable'] as well -- a presence check would read that as a yes."""
    assert _reset_gate({"x.com.samsung.da.filterResetType": ["replaceable"]})
    assert _reset_gate({"x.com.samsung.da.filterResetType": ["washable"]})
    assert not _reset_gate({"x.com.samsung.da.filterResetType": ["notresetable"]})
    assert not _reset_gate({"x.com.samsung.da.filterResetType": []})
    assert not _reset_gate({"x.com.samsung.da.filterUsage": "40"})


def test_water_filter_reset_survives_an_unfetched_stub():
    """An explicit exists_fn bypasses entity._is_included's stub carve-out,
    which would otherwise drop the button for the life of the config entry
    when /device/0 answers a not-yet-fetched {"href": ...} stub.

    A genuinely empty {} rep is the opposite case and must stay excluded --
    the device's confirmed answer that this resource will never populate,
    which issue #127 exists to keep distinct from a stub."""
    assert _reset_gate({"href": "/filter/waterfilter/vs/0"})
    assert not _reset_gate({})


def test_water_filter_reset_follows_the_devices_own_reset_claim():
    """The gate is filterResetType, so the button reaches every family that
    advertises one -- four fridges and three water purifiers here, of which
    only TP1X_REF_21K is hardware-verified. Pinned deliberately: the blast
    radius of trusting the device's claim should fail this test if it grows,
    rather than widening unnoticed."""
    for fixture in (
        "refrigerator_artik051_ref_17k",
        "refrigerator",
        "refrigerator_tp2x_ref_20k",
        "water_purifier",
        "water_purifier_coffee",
        "water_purifier_ailite_25k",
    ):
        assert _water_filter_desc(fixture, "filter_reset") is not None, fixture


@pytest.mark.parametrize(
    "capability,key,href",
    [
        (common.WATER_FILTER, "filter_reset", "/filter/waterfilter/vs/0"),
        (airconditioner.AIR_FILTER, "air_filter_reset", "/filter/airdustfilter/vs/0"),
        (airconditioner.AIR_FILTER_PM1, "air_filter_pm1_reset", "/filter/airdustPM1filter/vs/0"),
        (air_purifier.HEPA_FILTER, "hepa_filter_reset", "/filter/hepafilter/vs/0"),
        (fridge.AIR_FILTER, "air_filter_reset", "/filter/airdustfilter/vs/0"),
        (fridge.DEODOR_FILTER, "deodor_filter_reset", "/filter/deodorfilter/vs/0"),
        (range_hood.HOOD_FILTER, "hood_filter_reset", "/filter/hoodfilter/vs/0"),
    ],
)
def test_every_filter_reset_writes_to_its_own_resource(capability, key, href):
    """filterReset 'On' is a property of the x.com.samsung.da.filter.* resource
    type, confirmed on two unrelated families (a TP1X_REF_21K water filter and
    a TP1X_DA_AC_RAC_01001 air filter, #449). The factory derives the path from
    each capability's own href, so a button can never write to a sibling
    filter's resource -- which is the failure a shared descriptor invites."""
    desc = next((e for e in capability.entities if e.key == key), None)
    assert desc is not None, f"{key} missing from {capability.href}"
    assert capability.href == href
    assert desc.write_fn(desc.payload, {}) == (
        [s for s in href.strip("/").split("/") if s],
        {"x.com.samsung.da.filterReset": "On"},
    )


@pytest.mark.parametrize(
    "capability,key,href",
    [
        (common.WATER_FILTER, "filter_reset", "/filter/waterfilter/vs/0"),
        (airconditioner.AIR_FILTER, "air_filter_reset", "/filter/airdustfilter/vs/0"),
        (airconditioner.AIR_FILTER_PM1, "air_filter_pm1_reset", "/filter/airdustPM1filter/vs/0"),
        (air_purifier.HEPA_FILTER, "hepa_filter_reset", "/filter/hepafilter/vs/0"),
        (fridge.AIR_FILTER, "air_filter_reset", "/filter/airdustfilter/vs/0"),
        (fridge.DEODOR_FILTER, "deodor_filter_reset", "/filter/deodorfilter/vs/0"),
        (range_hood.HOOD_FILTER, "hood_filter_reset", "/filter/hoodfilter/vs/0"),
    ],
)
def test_filter_reset_survives_the_resources_aware_write_call(capability, key, href):
    """The coordinator offers every write_fn the four-argument
    (payload, rep, href, resources) form first and falls back to three on
    TypeError, so a write_fn's arity is part of its contract. Issue #461: a
    fourth parameter carrying the bound path segments swallowed `resources`
    instead, and the button posted to every href on the device at once
    ('path_segs must contain at most 32 values'). Called the way the
    coordinator calls it, the path must still be this filter's own."""
    desc = next(e for e in capability.entities if e.key == key)
    try:
        result = desc.write_fn(desc.payload, {}, href, {"/power/vs/0": {}, href: {}})
    except TypeError:
        result = desc.write_fn(desc.payload, {}, href)
    assert result == (
        [s for s in href.strip("/").split("/") if s],
        {"x.com.samsung.da.filterReset": "On"},
    )
