"""Tests for legacy_http -- the 8888/HTTPS envelope translation (issue #168).

Two levels. The small bodies below are hand-built from a live TP6X_WW6500
and pin down the shape of the translation -- the mechanical rule and each
of the three exceptions the table carries -- so a second appliance either
fits or shows exactly where it doesn't.

`TestAgainstTheDeviceDump` is the one that matters: it runs the real,
redacted dump in tests/fixtures/washer_tp6x_ww6500_8888.json (all nine
endpoints, captured in one pass) through the translation and then through
this repository's own `resolve()` and `discover()`, unmodified. Redacted
there: the serial, and the appliance's `uuid`, which on this board is its
MAC address in UUID form.
"""

import json
from pathlib import Path

from custom_components.localthings.legacy_http import (
    PREFIX,
    TP6X_WASHER,
    http_status_to_coap,
    to_resources,
    to_write,
    unwrap,
)
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover

FIXTURE = Path(__file__).parent / "fixtures" / "washer_tp6x_ww6500_8888.json"

# What `GET /devices/0` reports, plus the two resources that aggregate only
# links to, merged the way a caller would hand them over.
BODIES = {
    "Operation": {
        "state": "Run",
        "progress": "Rinse",
        "progressPercentage": 84,
        "remainingTime": "00:10:00",
        "supportedProgress": ["None", "Wash", "Rinse", "Spin", "Finish"],
        "power": "On",
        "kidsLock": "Ready",
    },
    "Mode": {
        "options": ["Course_5C", "LaundryOutTime_0", "DeviceType_0167"],
        "supportedOptions": ["35B8520520A204"],
    },
    "Washer": {
        "waterTemperature": "40",
        "rinseCycles": "2",
        "spinLevel": "1400",
        "supportedWaterTemperature": ["Cold", "20", "30", "40", "60", "95"],
    },
    "Configuration": {"remoteControlEnabled": True},
    "Information": {
        "modelID": "TP6X_WW6500|FF1BE000",
        "serialNumber": "REDACTED",
        "description": "TP6X_WASHER",
    },
    "Diagnosis": {"diagnosisStart": "Ready"},
    "Alarms": [{"id": "0", "code": "DrumClean", "state": "Created"}],
}


class TestToResources:
    def test_fields_take_the_samsung_prefix_and_keep_their_names(self):
        resources = to_resources(BODIES, TP6X_WASHER)

        assert resources["/washer/vs/0"] == {
            PREFIX + "waterTemperature": "40",
            PREFIX + "rinseCycles": "2",
            PREFIX + "spinLevel": "1400",
            PREFIX + "supportedWaterTemperature": ["Cold", "20", "30", "40", "60", "95"],
        }

    def test_mode_lands_on_course_not_on_mode(self):
        """This firmware's `Mode` carries the `Course_` token array, which
        is /course/vs/0's contract on the OCF side -- /mode/vs/0 is a
        different resource, and this appliance 404s it."""
        resources = to_resources(BODIES, TP6X_WASHER)

        assert "/mode/vs/0" not in resources
        assert resources["/course/vs/0"][PREFIX + "options"] == [
            "Course_5C",
            "LaundryOutTime_0",
            "DeviceType_0167",
        ]

    def test_operation_fans_out_power_and_the_child_lock(self):
        """The one structural exception: this firmware reports both inside
        `Operation`, where the OCF side has them as resources of their own
        and this repository's capabilities read them there."""
        resources = to_resources(BODIES, TP6X_WASHER)

        assert resources["/power/vs/0"] == {PREFIX + "power": "On"}
        assert resources["/kidslock/vs/0"] == {PREFIX + "kidsLock": "Ready"}
        assert PREFIX + "power" not in resources["/operational/state/vs/0"]

    def test_operational_state_keeps_the_rest(self):
        resources = to_resources(BODIES, TP6X_WASHER)

        assert resources["/operational/state/vs/0"] == {
            PREFIX + "state": "Run",
            PREFIX + "progress": "Rinse",
            PREFIX + "progressPercentage": 84,
            PREFIX + "remainingTime": "00:10:00",
            PREFIX + "supportedProgress": ["None", "Wash", "Rinse", "Spin", "Finish"],
        }

    def test_information_is_renamed_in_exactly_two_places(self):
        resources = to_resources(BODIES, TP6X_WASHER)
        info = resources["/information/vs/0"]

        assert info[PREFIX + "modelNum"] == "TP6X_WW6500|FF1BE000"
        assert info[PREFIX + "serialNum"] == "REDACTED"
        # Everything else is mechanical, including in the same resource.
        assert info[PREFIX + "description"] == "TP6X_WASHER"

    def test_alarms_become_an_items_array_of_prefixed_maps(self):
        resources = to_resources(BODIES, TP6X_WASHER)

        assert resources["/alarms/vs/0"] == {
            PREFIX + "items": [
                {
                    PREFIX + "id": "0",
                    PREFIX + "code": "DrumClean",
                    PREFIX + "state": "Created",
                }
            ]
        }

    def test_a_resource_the_appliance_did_not_report_is_absent(self):
        """Absent, not empty: an empty rep is a board's confirmed answer
        that it has none of this, which callers already read differently
        (registry.batch.is_stub_rep)."""
        resources = to_resources({k: v for k, v in BODIES.items() if k != "Diagnosis"})

        assert "/diagnosis/vs/0" not in resources

    def test_every_href_is_the_shape_a_device0_batch_produces(self):
        """The whole point of the translation: what comes out is keyed and
        shaped exactly like parse_device0_batch's output, so registry/ and
        discovery.py need no notion of which transport fed them."""
        resources = to_resources(BODIES, TP6X_WASHER)

        assert all(href.startswith("/") for href in resources)
        assert all(isinstance(rep, dict) for rep in resources.values())
        assert all(key.startswith(PREFIX) for rep in resources.values() for key in rep)


class TestToWrite:
    def test_one_step_wraps_the_resource_the_appliance_expects(self):
        body = to_write([("/course/vs/0", {PREFIX + "options": ["LaundryOutTime_60"]})])

        assert body == {"Device": {"Mode": {"options": ["LaundryOutTime_60"]}}}

    def test_steps_coalesce_into_a_single_aggregate_body(self):
        """The measured rule on this appliance: a cycle is accepted only in
        the same body as Operation.state, never on its own."""
        body = to_write(
            [
                ("/course/vs/0", {PREFIX + "options": ["Course_63"]}),
                ("/operational/state/vs/0", {PREFIX + "state": "Run"}),
            ]
        )

        assert body == {
            "Device": {
                "Mode": {"options": ["Course_63"]},
                "Operation": {"state": "Run"},
            }
        }

    def test_a_fanned_out_field_writes_back_into_its_own_wrapper(self):
        body = to_write([("/power/vs/0", {PREFIX + "power": "Off"})])

        assert body == {"Device": {"Operation": {"power": "Off"}}}

    def test_a_renamed_field_writes_under_its_wire_name(self):
        body = to_write([("/information/vs/0", {PREFIX + "modelNum": "X"})])

        assert body == {"Device": {"Information": {"modelID": "X"}}}

    def test_an_href_this_table_has_no_row_for_is_dropped(self):
        """Rather than inventing a wrapper for it: a write nobody can place
        must not reach the appliance as a guess."""
        assert to_write([("/energy/consumption/vs/0", {PREFIX + "cumulativePower": "1"})]) == {
            "Device": {}
        }

    def test_writes_round_trip_through_the_table(self):
        resources = to_resources(BODIES, TP6X_WASHER)
        body = to_write([(href, rep) for href, rep in resources.items() if href == "/washer/vs/0"])

        assert body["Device"]["Washer"] == BODIES["Washer"]


class TestHttpStatusToCoap:
    def test_success_statuses(self):
        assert http_status_to_coap(200) == 0x45  # 2.05 Content
        assert http_status_to_coap(204) == 0x44  # 2.04 Changed

    def test_client_errors(self):
        assert http_status_to_coap(403) == 0x83  # 4.03 Forbidden
        assert http_status_to_coap(404) == 0x84  # 4.04 Not Found

    def test_unmapped_statuses_fall_back_by_class(self):
        assert http_status_to_coap(418) == 0x80
        assert http_status_to_coap(503) == 0xA0


class TestAgainstTheDeviceDump:
    """The real appliance, through this repository's own detection.

    Deliberately asserts properties rather than counts: how many entities
    the washer registry binds is yours to change, and a test that pins the
    number would break on every capability you add. What must hold is that
    the translation leaves nothing unaccounted for and that the entities
    this family actually has still resolve.
    """

    @staticmethod
    def _resources():
        dump = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return to_resources(
            unwrap(
                dump["/devices/0"],
                dump["/devices/0/configuration"],
                dump["/devices/0/information"],
            )
        )

    def test_the_dump_produces_the_canonical_resource_set(self):
        assert set(self._resources()) == {
            "/alarms/vs/0",
            "/course/vs/0",
            "/diagnosis/vs/0",
            "/information/vs/0",
            "/kidslock/vs/0",
            "/operational/state/vs/0",
            "/power/vs/0",
            "/remotectrl/vs/0",
            "/washer/vs/0",
        }

    def test_the_device_types_as_a_washer(self):
        """Through `description` ('TP6X_WASHER') and the consumer-prefix
        rule, with no new board token and no change to by_type."""
        registry = resolve(self._resources())

        assert registry is not None
        assert registry.name == "washer"

    def test_nothing_is_left_unbound(self):
        resources = self._resources()
        registry = resolve(resources)
        unbound: list[str] = []

        discover(
            resources,
            registry.capabilities,
            registry.pattern_capabilities,
            log=unbound.append,
        )

        assert unbound == []

    def test_the_entities_this_appliance_has_are_bound(self):
        """Not an exhaustive list -- the ones whose absence would mean the
        translation lost something, each on a different canonical href."""
        resources = self._resources()
        registry = resolve(resources)

        bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
        keys = {b.key_override or b.desc.key for b in bound}

        assert {
            "cycle",  # /course/vs/0
            "machine_state",  # /operational/state/vs/0
            "progress_percentage",
            "wash_temperature",  # /washer/vs/0
            "spin_speed",
            "rinse_cycles",
            "power_switch",  # /power/vs/0 -- fanned out of Operation
            "child_lock",  # /kidslock/vs/0 -- likewise
            "remote_control",  # /remotectrl/vs/0
            "alarm_code",  # /alarms/vs/0
            "diagnosis_status",  # /diagnosis/vs/0
        } <= keys

    def test_the_cycle_select_offers_this_appliance_own_courses(self):
        """The supportedOptions blob decodes through your own decoder, on a
        board that publishes no editCourseList: 14 courses, which is what
        the dial has."""
        from custom_components.localthings.registry.capabilities.laundry import cycle_options

        assert len(cycle_options(self._resources())) == 14
