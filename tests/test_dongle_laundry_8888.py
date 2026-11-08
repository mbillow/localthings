"""The DONGLE_WM washer (#762) and DONGLE_DRY dryer (#761) over the 8888
bridge: routing from the record's own `type`, and the dryer's heat setting."""

import json
from pathlib import Path

import pytest

from custom_components.localthings.legacy_http import table_for, to_resources, with_record_type
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import dryer
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc

FIXTURES = Path(__file__).parent / "fixtures"
DRYER = "dryer_dongle_dv45k6500ex_8888"
WASHER = "washer_dongle_wf7500_8888"
TEMPERATURE = "x.com.samsung.da.waterTemperature"


def _bodies(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["bodies"]


def _resources(name):
    bodies = _bodies(name)
    return to_resources(with_record_type(bodies, bodies["type"]), table_for(bodies["type"]))


def _state(resources):
    registry = resolve(resources)
    assert registry is not None
    bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    return flatten(bound, resources)


def _heat() -> SelectDesc:
    desc = next(d for d in dryer.DRYER_SETTINGS.entities if d.key == "drying_temperature")
    assert isinstance(desc, SelectDesc)
    return desc


def _on_course(resources, course):
    rep = dict(resources["/course/vs/0"])
    rep["x.com.samsung.da.options"] = [
        f"Course_{course}" if o.startswith("Course_") else o
        for o in rep["x.com.samsung.da.options"]
    ]
    return {**resources, "/course/vs/0": rep}


class TestRouting:
    @pytest.mark.parametrize(("name", "registry"), [(DRYER, "dryer"), (WASHER, "washer")])
    def test_the_record_type_routes_it(self, name, registry):
        resolved = resolve(_resources(name))

        assert resolved is not None
        assert resolved.name == registry

    @pytest.mark.parametrize("name", [DRYER, WASHER])
    def test_without_the_record_type_it_is_unrecognized(self, name):
        bodies = _bodies(name)

        assert resolve(to_resources(bodies, table_for(bodies["type"]))) is None

    @pytest.mark.parametrize("record_type", ["Washer", "Dryer"])
    def test_a_reported_subtype_is_kept(self, record_type):
        bodies = {"Information": {"deviceSubType": "Washer"}}

        assert with_record_type(bodies, record_type) == bodies

    @pytest.mark.parametrize("name", [DRYER, WASHER])
    def test_nothing_is_left_unbound(self, name):
        resources = _resources(name)
        registry = resolve(resources)
        assert registry is not None
        unbound: list[str] = []

        discover(
            resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
        )

        assert unbound == []


class TestEntities:
    def test_the_washer_settings_read_through(self):
        state = _state(_resources(WASHER))

        assert state["wash_temperature"] == "Warm"
        assert state["spin_speed"] == "High"
        assert state["soil_level"] == "Normal"

    def test_the_dryer_settings_read_through(self):
        state = _state(_resources(DRYER))

        assert state["drying_temperature"] == "Medium"
        assert state["dry_level"] == "Normal"
        assert state["wrinkle_prevent"] is False


class TestHeat:
    """The dryer's heat setting rides on waterTemperature (#79, #761)."""

    def test_normal_offers_only_its_fixed_heat(self):
        """Course 01's mask allows only Medium on this dryer."""
        assert _heat().options(_resources(DRYER)) == ["Medium"]

    def test_time_dry_offers_every_heat(self):
        """Confirmed on the DV45K6500EW: Time Dry (7F) offers ExtraLow to
        High, and a Low picked here ran as Low when started (#761)."""
        resources = _on_course(_resources(DRYER), "7F")

        assert _heat().options(resources) == ["ExtraLow", "Low", "MediumLow", "Medium", "High"]

    def test_the_heat_writes_the_washer_resource(self):
        rep = _resources(DRYER)["/washer/vs/0"]
        write = _heat().write_fn

        assert write is not None
        assert write("Low", rep) == (["washer", "vs", "0"], {TEMPERATURE: "Low"})

    def test_a_dryer_without_the_field_gets_no_heat_select(self):
        exists = _heat().exists_fn

        assert exists is not None
        assert exists({"x.com.samsung.da.dryLevel": "Normal"}, {}) is False
