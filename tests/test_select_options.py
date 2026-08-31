"""Tests for LocalThingsSelect's option-list resolution
(custom_components/localthings/select.py) -- the static tuple, options_field,
and callable forms of SelectDesc.options.
"""

from typing import ClassVar, cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import dryer
from custom_components.localthings.registry.capabilities.laundry import (
    BUZZER_SOUND,
    cycle_select,
    washer_cycle_fallback,
)
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.select import LocalThingsSelect


class _FakeCoordinator:
    device_key = "TEST-SERIAL"

    def __init__(self, last_resources):
        self.last_resources = last_resources

    def canonical_resources(self, subdevice):
        # Every entity built by _make_select uses the default MAIN
        # subdevice, so the canonical view is just the raw snapshot
        # (issue #177 -- see LocalThingsEntity._resources).
        return self.last_resources


def _make_select(desc, href, last_resources):
    capability = Capability(href=href, entities=(desc,))
    bound = BoundEntity(href=href, capability=capability, desc=desc)
    return LocalThingsSelect(cast(LocalThingsCoordinator, _FakeCoordinator(last_resources)), bound)


def test_static_options_unaffected():
    desc = SelectDesc(key="x", options=("A", "B"))
    entity = _make_select(desc, "/x/vs/0", {})
    assert entity.options == ["A", "B"]


def test_options_field_unaffected():
    desc = SelectDesc(key="x", options_field="supported")
    entity = _make_select(desc, "/x/vs/0", {"/x/vs/0": {"supported": ["Lo", "Hi"]}})
    assert entity.options == ["Lo", "Hi"]


def test_buzzer_volume_options_normalize_to_translation_keys():
    desc = next(e for e in BUZZER_SOUND.entities if e.key == "buzzer_sound")
    entity = _make_select(
        desc,
        "/buzzersound/vs/0",
        {
            "/buzzersound/vs/0": {
                "supportedBuzzerSound": [
                    "Volume_Off",
                    "Volume_Low",
                    "Volume_Med",
                    "Volume_High",
                ]
            }
        },
    )
    assert entity.options == ["volume_off", "volume_low", "volume_med", "volume_high"]


def _dry_level_desc():
    return next(e for e in dryer.DRYER_SETTINGS.entities if e.key == "dry_level")


def test_dryer_dry_level_word_vocabulary_normalizes_to_translation_keys():
    """Damp/Less/Normal/More/Very are catalogued under dryer_dry_level, so
    they normalize to lowercase state keys the same way
    test_buzzer_volume_options_normalize_to_translation_keys does -- Home
    Assistant's frontend resolves the displayed text from there."""
    entity = _make_select(
        _dry_level_desc(),
        "/washer/vs/0",
        {
            "/washer/vs/0": {
                "x.com.samsung.da.dryLevel": "Normal",
                "x.com.samsung.da.supportedDryLevel": [
                    "None",
                    "Damp",
                    "Less",
                    "Normal",
                    "More",
                    "Very",
                ],
            }
        },
    )
    assert entity.options == ["none", "damp", "less", "normal", "more", "very"]


def test_dryer_dry_level_numeric_vocabulary_renders_raw():
    """DV6800N reports supportedDryLevel as None/1/2/3 rather than the
    confirmed words. 'None' still normalizes (it is in the catalog); the
    digits have no catalog entry, so they pass through unchanged instead of
    being guessed at."""
    entity = _make_select(
        _dry_level_desc(),
        "/washer/vs/0",
        {
            "/washer/vs/0": {
                "x.com.samsung.da.dryLevel": "2",
                "x.com.samsung.da.supportedDryLevel": ["None", "1", "2", "3"],
            }
        },
    )
    assert entity.options == ["none", "1", "2", "3"]


def test_callable_options_receives_full_resource_snapshot():
    """A callable options is handed the coordinator's full href->rep
    snapshot, not just this entity's own href's rep -- needed for course
    lists decoded from a sibling resource (see laundry.cycle_options)."""
    calls = []

    def _options_fn(resources):
        calls.append(resources)
        return list(resources.get("/other/vs/0", {}).get("codes", []))

    desc = SelectDesc(key="cycle", translation_key="fake_cycle", options=_options_fn)
    resources = {
        "/x/vs/0": {},
        "/other/vs/0": {"codes": ["1C", "1D"]},
    }
    entity = _make_select(desc, "/x/vs/0", resources)
    assert entity.options == ["1C", "1D"]
    assert calls == [resources]


def test_callable_options_empty_result():
    desc = SelectDesc(key="cycle", options=lambda resources: [])
    entity = _make_select(desc, "/x/vs/0", {})
    assert entity.options == []


def test_callable_translation_key_reresolves_live_not_once_at_construction():
    """A callable translation_key (laundry.cycle_select's table-id-gated
    resolver) must be re-evaluated against current coordinator data on
    every access, not baked in once at __init__ -- discovery can run while
    a sibling resource (e.g. /st/washercourse/vs/0) is still an empty stub
    (see entity.py's _is_included docstring), and a one-time resolution
    would permanently show untranslated codes even after a later poll
    populates the real value."""
    desc = SelectDesc(key="cycle", translation_key=lambda resources: resources.get("key"))
    resources = {"key": None}
    entity = _make_select(desc, "/x/vs/0", resources)
    assert entity.translation_key is None

    resources["key"] = "washer_cycle_table_02"
    assert entity.translation_key == "washer_cycle_table_02"


async def test_unknown_vendor_option_round_trips_to_exact_raw_value():
    """Readable fallback labels must still write the exact Samsung token."""

    class _WritableCoordinator(_FakeCoordinator):
        data: ClassVar[dict] = {"mode": "FutureVendorMode"}

        def __init__(self, last_resources):
            super().__init__(last_resources)
            self.writes = []

        async def async_send_command(self, bound, value):
            self.writes.append(value)

    desc = SelectDesc(
        key="mode",
        translation_key="door_alert",
        options=("Known", "FutureVendorMode"),
        write_fn=lambda *args: None,
    )
    capability = Capability(href="/x/vs/0", entities=(desc,))
    bound = BoundEntity(href="/x/vs/0", capability=capability, desc=desc)
    coordinator = _WritableCoordinator({})
    entity = LocalThingsSelect(cast(LocalThingsCoordinator, coordinator), bound)

    assert entity.options[-1] == "Future Vendor Mode"
    await entity.async_select_option("Future Vendor Mode")
    assert coordinator.writes == ["FutureVendorMode"]


async def test_washer_diagnostic_cycle_values_share_one_display_and_write_path():
    """Regression for Course_69/EditCourseList_696F... from real hardware."""

    class _WritableCoordinator(_FakeCoordinator):
        data: ClassVar[dict] = {"cycle": "69"}

        def __init__(self, last_resources):
            super().__init__(last_resources)
            self.writes = []

        async def async_send_command(self, bound, value):
            self.writes.append(value)

    desc = cycle_select(
        translation_key="washer_cycle",
        icon="mdi:washing-machine",
        table_href="/st/washercourse/vs/0",
        display_fn=washer_cycle_fallback,
    )
    capability = Capability(href="/course/vs/0", entities=(desc,))
    bound = BoundEntity(href="/course/vs/0", capability=capability, desc=desc)
    resources = {
        "/course/vs/0": {"x.com.samsung.da.options": ["Course_69"]},
        "/st/washercourse/vs/0": {
            "x.com.samsung.da.st.courseTable": "Table_02",
        },
        "/wm/editcourse/vs/0": {
            "x.com.samsung.da.editCourseList": (
                "EditCourseList_696F757801719688706D6A7376726C6E6B777479F1F3"
            ),
        },
        "/wm/personalcourse/vs/0": {
            "x.com.samsung.da.courses": [
                "F1_0106EC868DEC98B7",
                "F3_0109EC9A94EAB8B0EBB3B4",
            ],
        },
    }
    coordinator = _WritableCoordinator(resources)
    entity = LocalThingsSelect(cast(LocalThingsCoordinator, coordinator), bound)
    first_name = bytes.fromhex("EC868DEC98B7").decode("utf-8")
    second_name = bytes.fromhex("EC9A94EAB8B0EBB3B4").decode("utf-8")

    # Known catalog states stay as HA translation keys; the frontend renders
    # this confirmed Table_02 mapping as "AI Wash".
    assert entity.current_option == "69"
    assert entity.options == [
        "69",
        "6f",
        "75",
        "78",
        "01",
        "71",
        "96",
        "88",
        "70",
        "6d",
        "6a",
        "73",
        "76",
        "72",
        "6c",
        "6e",
        "6b",
        "77",
        "74",
        "79",
        first_name,
        second_name,
    ]

    await entity.async_select_option("6f")
    await entity.async_select_option(first_name)
    assert coordinator.writes == ["6F", "F1"]
