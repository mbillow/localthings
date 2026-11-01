"""The RR40M7165WW behind a DONGLE_0281 8888 bridge (#698): routing from the
record's own `type`, the Fridge wrapper, and the Temperatures setpoint."""

import json
from pathlib import Path

from custom_components.localthings.legacy_http import (
    table_for,
    to_resources,
    to_write,
    with_record_type,
)
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.capabilities.common import remote_control_enabled
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import NumberDesc, SwitchDesc

CAPTURE = Path(__file__).parent / "fixtures" / "refrigerator_dongle_0281_8888.json"
TEMPERATURES = "/temperatures/vs/0"


def _bodies():
    return json.loads(CAPTURE.read_text(encoding="utf-8"))["bodies"]


def _resources():
    bodies = _bodies()
    return to_resources(with_record_type(bodies, bodies["type"]), table_for(bodies["type"]))


def _state(resources):
    registry = resolve(resources)
    assert registry is not None
    bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    return flatten(bound, resources)


def _desc(key) -> NumberDesc:
    desc = next(d for d in fridge.TEMPERATURES_FALLBACK.entities if d.key == key)
    assert isinstance(desc, NumberDesc)
    return desc


class TestRouting:
    def test_the_record_type_routes_it_to_the_fridge_registry(self):
        registry = resolve(_resources())

        assert registry is not None
        assert registry.name == "refrigerator"

    def test_a_reported_subtype_is_kept(self):
        bodies = {"Information": {"deviceSubType": "Kimchi"}}

        assert with_record_type(bodies, "Refrigerator") == bodies

    def test_other_record_types_are_not_taken_as_subtypes(self):
        """A hood slot's record says `Cooktop` (#676)."""
        bodies = {"Information": {"description": "TP6X_CT_HOOD"}}

        assert with_record_type(bodies, "Cooktop") == bodies

    def test_without_the_record_type_it_is_unrecognized(self):
        bodies = _bodies()

        assert resolve(to_resources(bodies, table_for(bodies["type"]))) is None

    def test_nothing_is_left_unbound(self):
        resources = _resources()
        registry = resolve(resources)
        assert registry is not None
        unbound: list[str] = []

        discover(
            resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
        )

        assert unbound == []


class TestEntities:
    def test_the_fridge_compartment_reads_through(self):
        state = _state(_resources())

        assert state["fridge_temperature"] == 5
        assert state["fridge_setpoint"] == 5
        assert state["rapid_fridge"] is False
        assert state["door_open"] is False

    def test_compartments_and_flags_it_does_not_report_are_absent(self):
        """Freezer and CVRoom items carry only an id and a unit, and the
        Fridge wrapper has no rapidFreezing."""
        state = _state(_resources())

        assert "freezer_temperature" not in state
        assert "freezer_setpoint" not in state
        assert "rapid_freezing" not in state

    def test_a_region_only_configuration_does_not_read_as_remote_control_off(self):
        resources = _resources()

        assert "/remotectrl/vs/0" not in resources
        assert remote_control_enabled(resources) is True
        assert "remote_control" not in _state(resources)

    def test_the_setpoint_bounds_are_the_items_own(self):
        rep = _resources()[TEMPERATURES]
        desc = _desc("fridge_setpoint")

        assert desc.native_min_fn is not None and desc.native_max_fn is not None
        assert (desc.native_min_fn(rep), desc.native_max_fn(rep)) == (1.0, 7.0)
        assert desc.unit_fn is not None and desc.unit_fn(rep) == "°C"


class TestWrites:
    def test_the_setpoint_writes_the_fridge_item_by_its_id(self):
        rep = _resources()[TEMPERATURES]
        desc = _desc("fridge_setpoint")
        assert desc.write_fn is not None

        result = desc.write_fn(4, rep)
        assert result is not None
        path, body = result

        assert path == ["temperatures", "vs", "0"]
        assert to_write([(TEMPERATURES, body)], table_for("Refrigerator")) == {
            "Device": {"Temperatures": [{"id": "1", "desired": 4}]}
        }

    def test_rapid_fridge_writes_the_fridge_wrapper(self):
        desc = next(d for d in fridge.REFRIGERATION.entities if d.key == "rapid_fridge")
        assert isinstance(desc, SwitchDesc) and desc.write_fn is not None

        result = desc.write_fn("On", {})
        assert result is not None
        path, body = result

        assert path == ["refrigeration", "vs", "0"]
        assert to_write([("/refrigeration/vs/0", body)], table_for("Refrigerator")) == {
            "Device": {"Fridge": {"rapidFridge": "On"}}
        }


class TestVacationMode:
    """Unconfirmed on hardware: the bridge lists RVACATION_ON/OFF but no
    dump has ever shown it on (#698)."""

    @staticmethod
    def _desc() -> SwitchDesc:
        desc = next(d for d in fridge.FLEX_ZONE.entities if d.key == "vacation_mode")
        assert isinstance(desc, SwitchDesc)
        return desc

    def test_it_reads_off(self):
        assert _state(_resources())["vacation_mode"] is False

    def test_turning_it_on_swaps_only_its_token(self):
        rep = _resources()["/mode/vs/0"]
        modes = rep["x.com.samsung.da.modes"]
        write = self._desc().write_fn
        assert write is not None

        result = write("On", rep)
        assert result is not None
        path, body = result

        assert path == ["mode", "vs", "0"]
        assert body == {"x.com.samsung.da.modes": ["RVACATION_ON", *modes[1:]]}
        assert to_write([("/mode/vs/0", body)], table_for("Refrigerator")) == {
            "Device": {"Mode": {"modes": ["RVACATION_ON", *modes[1:]]}}
        }

    def test_a_board_without_the_on_value_gets_no_switch(self):
        """The AILITE/TP1X fridges list a VACATION_MODE_CONTROL flag instead."""
        rep = {
            "x.com.samsung.da.modes": ["RVACATION_OFF", "FCONVERT_FREEZER"],
            "x.com.samsung.da.supportedModes": ["VACATION_MODE_CONTROL"],
        }
        exists = self._desc().exists_fn
        assert exists is not None

        assert exists(rep, {}) is False
