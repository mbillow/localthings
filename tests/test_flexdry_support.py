"""FlexDry drum and upper-section support from their legacy wrappers."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import _is_included
from custom_components.localthings.legacy_http import course_table, table_for, to_resources
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import dryer
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc

FIXTURES = Path(__file__).parent / "fixtures"


def test_flexdry_sections_match_golden_entities_and_course_codes():
    sections = json.loads((FIXTURES / "flexdry_sections.json").read_text())
    sections.pop("seeds_note", None)
    golden = json.loads((FIXTURES / "golden" / "flexdry_sections.json").read_text())
    cycle = next(
        entity
        for entity in dryer.DRYER_COURSE.entities
        if entity.key == "cycle" and isinstance(entity, SelectDesc)
    )
    rep_fn = cycle.rep_fn
    assert rep_fn is not None

    for name, wrappers in sections.items():
        family = wrappers["Information"]["description"]
        resources = to_resources(wrappers, table_for(family))
        resources.update(course_table(family, indexed=name == "upper"))
        info = resources["/information/vs/0"]
        registry = for_device_by_model(
            info["x.com.samsung.da.modelNum"], info["x.com.samsung.da.description"]
        )
        assert registry is not None and registry.name == "dryer"

        bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
        coordinator = cast(
            LocalThingsCoordinator,
            SimpleNamespace(
                discovery_resources=resources,
                discovery_canonical=lambda _, current_resources=resources: current_resources,
            ),
        )
        included = [entity for entity in bound if _is_included(entity, coordinator)]
        state = flatten(included, resources)
        expected = golden[name]
        assert sorted(state) == expected["state_keys"]
        assert [code.upper() for code in cycle.options(resources)] == expected["cycle_codes"]
        assert rep_fn(resources["/course/vs/0"]).upper() == expected["current_course_code"]
        assert cycle.translation_key(resources) == "dryer_cycle_table_00"
