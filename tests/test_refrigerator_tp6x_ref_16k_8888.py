"""The RF28K9380SR/AA (TP6X_REF_16K) over 8888 (#749): doors named by their
own descriptions, the CVRoom's mode from supportedModes, and the defrost
delay, ice maker and water filter its board keeps on shared resources."""

import copy
import json
from pathlib import Path

import pytest

from custom_components.localthings.entity import _instance_display_name
from custom_components.localthings.legacy_http import (
    table_for,
    to_resources,
    to_write,
    with_record_type,
)
from custom_components.localthings.registry.adapter import _key, flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import fridge
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc, SwitchDesc

CAPTURE = Path(__file__).parent / "fixtures" / "refrigerator_tp6x_ref_16k_8888.json"
TABLE = table_for("Refrigerator")


def _bodies():
    return json.loads(CAPTURE.read_text(encoding="utf-8"))["bodies"]


def _resources(bodies=None):
    bodies = bodies if bodies is not None else _bodies()
    return to_resources(with_record_type(bodies, bodies["type"]), TABLE)


def _bound(resources):
    registry = resolve(resources)
    assert registry is not None
    assert registry.name == "refrigerator"
    return discover(resources, registry.capabilities, registry.pattern_capabilities)


def _state(bodies=None):
    resources = _resources(bodies)
    return flatten(_bound(resources), resources)


def _with_door(which, **fields):
    bodies = copy.deepcopy(_bodies())
    for item in bodies["Doors"]:
        if item.get("description") == which:
            item.update(fields)
    return bodies


def to_write_body(href, body):
    return to_write([(href, body)], TABLE)["Device"]


def _write(desc, payload, rep):
    assert desc.write_fn is not None
    result = desc.write_fn(payload, rep)
    assert result is not None
    return result


def test_nothing_is_left_unbound():
    resources = _resources()
    registry = resolve(resources)
    assert registry is not None
    unbound: list[str] = []

    discover(resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append)

    assert unbound == []


class TestDoors:
    def test_each_described_door_gets_its_own_sensor(self):
        state = _state()

        assert state["door_freezer_open"] is False
        assert state["door_fridge_open"] is False
        assert state["door_cvroom_open"] is False
        assert state["door_open"] is False
        assert not any(f"door_{n}_open" in state for n in range(1, 5))

    @pytest.mark.parametrize("description", ["Freezer", "Fridge", "CVRoom"])
    def test_a_door_reads_its_own_item(self, description):
        state = _state(_with_door(description, openState="Open"))
        key = f"door_{description.lower()}_open"

        assert {k for k, v in state.items() if k.startswith("door_") and v} == {"door_open", key}

    def test_names_come_from_the_description(self):
        """Through the same {instance_name} as DOOR_GENERIC's /door/freezer/0."""
        names = {
            _key(b): _instance_display_name(b, _key(b))
            for b in _bound(_resources())
            if b.desc.use_instance_name and b.href == "/doors/vs/0"
        }

        assert names == {
            "door_freezer_open": "Door Freezer",
            "door_fridge_open": "Door Fridge",
            "door_cvroom_open": "Door Cvroom",
        }

    def test_a_description_no_catalog_lists_still_gets_a_door(self):
        bodies = _with_door("Homebar", openState="Open")
        state = _state(bodies)

        assert state["door_homebar_open"] is True

    def test_a_door_that_stops_reporting_reads_unknown(self):
        resources = _resources()
        bound = _bound(resources)
        later = _resources(_with_door("CVRoom", description="Other"))

        assert flatten(bound, later)["door_cvroom_open"] is None

    def test_two_doors_alike_are_numbered(self):
        bodies = _with_door("CVRoom", description="Fridge")
        state = _state(bodies)

        assert "door_fridge_open" not in state
        assert state["door_1_open"] is False
        assert state["door_3_open"] is False


class TestFlexZone:
    @staticmethod
    def _desc() -> SelectDesc:
        desc = next(d for d in fridge.FLEX_ZONE.entities if d.key == "flex_zone_mode")
        assert isinstance(desc, SelectDesc)
        return desc

    def test_it_reads_the_zones_token(self):
        assert _state()["flex_zone_mode"] == "CV_TTYPE_FREEZER"

    def test_it_offers_its_own_familys_modes(self):
        """supportedModes lists every family's (CV_SBS_, CV_FDR_, ...)."""
        options = self._desc().options(_resources())

        assert options == [
            "CV_TTYPE_FREEZER",
            "CV_TTYPE_SOFT_FREEZER",
            "CV_TTYPE_CHILL",
            "CV_TTYPE_COOL",
            "CV_TTYPE_FERMENT",
            "CV_TTYPE_KIMCHI_WARM",
            "CV_TTYPE_KIMCHI_NORMAL",
            "CV_TTYPE_KIMCHI_COLD",
        ]

    def test_a_write_sends_only_its_token(self):
        rep = _resources()["/mode/vs/0"]
        path, body = _write(self._desc(), "CV_TTYPE_COOL", rep)

        assert path == ["mode", "vs", "0"]
        assert to_write_body("/mode/vs/0", body) == {"Mode": {"modes": ["CV_TTYPE_COOL"]}}

    def test_another_familys_mode_is_not_written(self):
        rep = _resources()["/mode/vs/0"]
        write = self._desc().write_fn
        assert write is not None

        assert write("CV_FDR_WINE", rep) is None


class TestWaterFilter:
    def test_disable_reads_normal(self):
        assert _state()["filter_status"] == "normal"

    def test_enable_reads_replace(self):
        bodies = copy.deepcopy(_bodies())
        modes = bodies["Mode"]["modes"]
        modes[modes.index("WATERFILTER_DISABLE")] = "WATERFILTER_ENABLE"

        assert _state(bodies)["filter_status"] == "replace"

    def test_the_filter_resource_wins_where_present(self):
        resources = _resources()
        resources["/filter/waterfilter/vs/0"] = {"x.com.samsung.da.filterStatus": "replace"}
        desc = next(d for d in fridge.FLEX_ZONE.entities if d.key == "filter_status")
        assert desc.exists_fn is not None

        assert not desc.exists_fn(resources["/mode/vs/0"], resources)


class TestRefrigerationSwitches:
    @staticmethod
    def _desc(key) -> SwitchDesc:
        desc = next(d for d in fridge.REFRIGERATION.entities if d.key == key)
        assert isinstance(desc, SwitchDesc)
        return desc

    @pytest.mark.parametrize(
        ("key", "wire"), [("defrost_delay", "delayDefrost"), ("ice_maker_enabled", "iceMaker")]
    )
    def test_it_reads_and_writes_the_fridge_wrapper(self, key, wire):
        assert _state()[key] is False
        path, body = _write(self._desc(key), "On", {})

        assert path == ["refrigeration", "vs", "0"]
        assert to_write_body("/refrigeration/vs/0", body) == {"Fridge": {wire: "On"}}

    @pytest.mark.parametrize(
        ("key", "href"),
        [("defrost_delay", "/defrost/delay/vs/0"), ("ice_maker_enabled", "/icemaker/status/vs/0")],
    )
    def test_a_dedicated_resource_wins_where_present(self, key, href):
        resources = _resources()
        resources[href] = {}
        desc = self._desc(key)
        assert desc.exists_fn is not None

        assert not desc.exists_fn(resources["/refrigeration/vs/0"], resources)
