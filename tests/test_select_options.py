"""Tests for LocalThingsSelect's option-list resolution
(custom_components/localthings/select.py) -- the static tuple, options_field,
and callable forms of SelectDesc.options.
"""

from typing import ClassVar, cast

import pytest

from custom_components.localthings import cloudcourse
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.capabilities import dryer, oven
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


def _make_select(desc, href, last_resources, coordinator_cls=_FakeCoordinator):
    capability = Capability(href=href, entities=(desc,))
    bound = BoundEntity(href=href, capability=capability, desc=desc)
    return LocalThingsSelect(cast(LocalThingsCoordinator, coordinator_cls(last_resources)), bound)


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


class TestDryLevelNarrowing:
    """dry_level's options follow the selected course (laundry.
    course_narrowed_options): the board advertises every level it supports,
    but each course only accepts a subset, and a write outside that subset
    is silently ignored by the appliance. Narrowing turns that no-op into
    Home Assistant's own ServiceValidationError.

    A record here is `<course:1><kind:nibble><default:nibble><mask:1>` after
    a 1-nibble group-count header, so "1" + "01D01E" is course 01 with one
    0xD (dry) group, default 0 and a mask of 0b00011110 -- indices 1-4. Each
    fixture carries a second course because _course_records rejects a
    one-record table as indistinguishable from garbage.
    """

    _SUPPORTED: ClassVar[list[str]] = ["None", "Damp", "Less", "Normal", "More"]

    def _entity(self, options, dry_level="Normal", supported=None):
        class _Coordinator(_FakeCoordinator):
            # What flatten() would have produced; only the live-value test
            # reads it back through current_option.
            data: ClassVar[dict] = {"dry_level": dry_level}

        return _make_select(
            _dry_level_desc(),
            "/washer/vs/0",
            {
                "/washer/vs/0": {
                    "x.com.samsung.da.dryLevel": dry_level,
                    "x.com.samsung.da.supportedDryLevel": (
                        self._SUPPORTED if supported is None else supported
                    ),
                },
                "/course/vs/0": options,
            },
            coordinator_cls=_Coordinator,
        )

    def test_mask_drops_the_levels_the_course_refuses(self):
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_01"],
                "x.com.samsung.da.supportedOptions": ["101D01E02D000"],
            }
        )
        assert entity.options == ["damp", "less", "normal", "more"]

    def test_no_decodable_opinion_keeps_the_full_supported_list(self):
        """A board with no supportedOptions at all says nothing about which
        levels its courses accept. course_option_mask returns None there,
        and 'no opinion' must not read as 'nothing allowed'."""
        entity = self._entity({"x.com.samsung.da.options": ["Course_01"]})
        assert entity.options == ["none", "damp", "less", "normal", "more"]

    def test_the_live_value_is_never_narrowed_away(self):
        """options and current_option are computed independently, and HA's
        SelectEntity.state returns None when the current option is missing
        from options -- so a course change landing before the board updates
        dryLevel would blank the entity. The union prevents that."""
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_01"],
                "x.com.samsung.da.supportedOptions": ["101D01E02D000"],
            },
            dry_level="None",  # index 0, outside the mask
        )
        assert entity.options == ["none", "damp", "less", "normal", "more"]
        # The point of the union: HA reads state as None when this fails.
        assert entity.current_option in entity.options

    def test_an_empty_mask_falls_back_to_the_live_value(self):
        """A course that advertises nothing selectable (a dryer's Quick Dry)
        must not leave a live entity with an empty dropdown -- that is the
        'unpopulated' contract, which pairs with exists_fn suppression this
        descriptor deliberately does not have."""
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_01"],
                "x.com.samsung.da.supportedOptions": ["101D00002D01E"],
            }
        )
        assert entity.options == ["normal"]

    def test_an_unpopulated_rep_still_yields_no_options(self):
        """No supportedDryLevel yet is the one case that legitimately gives
        an empty list: narrowing must not invent a single-entry dropdown out
        of a live value on a rep that has not been polled."""
        entity = self._entity(
            {"x.com.samsung.da.options": ["Course_01"]},
            supported=[],
        )
        assert entity.options == []

    def test_an_empty_mask_on_the_download_course_is_not_a_refusal(self):
        """A cloud Download slot reports empty masks for every kind while its
        values stay live -- the downloaded program supplies its own, and the
        board keeps taking writes (see the module comment above
        OPTION_KIND_*). Collapsing to the live value there would make every
        other level raise on a course that actually accepts them.

        Course 02 is this device's confirmed Download course and is the live
        selection, so its all-zero mask carries no opinion.
        """
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_02"],
                "x.com.samsung.da.supportedOptions": ["101D01E02D000"],
                cloudcourse.FIELD: {"download_course": "02", "programs": {}},
            }
        )
        assert entity.options == ["none", "damp", "less", "normal", "more"]

    def test_an_empty_mask_on_a_local_course_still_falls_back(self):
        """The same bytes without the Download marker keep the old meaning --
        a dryer's Quick Dry genuinely allows nothing."""
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_02"],
                "x.com.samsung.da.supportedOptions": ["101D01E02D000"],
                cloudcourse.FIELD: {"download_course": "01", "programs": {}},
            }
        )
        assert entity.options == ["normal"]

    def test_entries_a_one_byte_mask_cannot_address_are_kept(self):
        """The mask is one byte, so it can only speak about indices 0-7. A
        longer supported list is one it partially describes, not one whose
        tail it refuses -- and over-offering costs at most a write the
        appliance rejects, while under-offering makes a value the user can
        really select unreachable.
        """
        supported = [f"L{i}" for i in range(11)]
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_01"],
                # Mask 0b00000110 -- indices 1 and 2 of the addressable range.
                "x.com.samsung.da.supportedOptions": ["101D00602D000"],
            },
            dry_level="L1",
            supported=supported,
        )
        assert entity.options == ["L1", "L2", "L8", "L9", "L10"]

    def test_the_supported_list_sets_the_order_not_the_mask(self):
        """The dropdown must not reshuffle as the course changes."""
        entity = self._entity(
            {
                "x.com.samsung.da.options": ["Course_01"],
                "x.com.samsung.da.supportedOptions": ["101D01E02D000"],
            }
        )
        assert entity.options == sorted(
            entity.options, key=["none", "damp", "less", "normal", "more"].index
        )


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


@pytest.mark.parametrize(
    "raw,state",
    [
        ("UpperHealthycook4", "upper_healthycook4"),
        ("Healthycook6", "healthycook6"),
        ("LowerConvectionRoast", "lower_convection_roast"),
        ("Autocook", "autocook"),
        ("PyroFree", "pyro_free"),
    ],
)
async def test_oven_mode_labels_preserve_the_device_command(raw, state):
    class _WritableCoordinator(_FakeCoordinator):
        def __init__(self, resources):
            super().__init__(resources)
            self.data = {"oven_mode": raw}
            self.writes = []

        async def async_send_command(self, bound, value):
            self.writes.append(value)

    desc = next(e for e in oven.OVEN_MODE.entities if e.key == "oven_mode")
    coordinator = _WritableCoordinator({"/mode/vs/0": {"x.com.samsung.da.supportedModes": [raw]}})
    bound = BoundEntity(href="/mode/vs/0", capability=oven.OVEN_MODE, desc=desc)
    entity = LocalThingsSelect(cast(LocalThingsCoordinator, coordinator), bound)

    assert entity.current_option == state
    assert state in entity.options
    await entity.async_select_option(state)
    assert coordinator.writes == [raw]


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
