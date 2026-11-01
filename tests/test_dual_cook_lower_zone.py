"""The LCD_R18 Dual Cook's lower zone (#597) and modeSpecs remembered across
a board that answers `"[]"` (#600).

The NV7B6665IAA's `/device/1` lists no modes of its own; the ones it runs
are the ones `/mode/vs/0`'s modeSpec tags `cavity: Lower`. The resources
below are trimmed from the reads in #597 and #600.
"""

from __future__ import annotations

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.localthings.coordinator import mode_spec_store
from custom_components.localthings.diagnostics import async_get_config_entry_diagnostics
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import cook, oven
from custom_components.localthings.registry.registry import CAPABILITIES
from custom_components.localthings.registry.subdevices import (
    MAIN,
    Subdevice,
    canonical_view,
    discover_partitioned,
)
from tests.test_coordinator_cook_start import _posts, bare_coordinator  # noqa: F401

LOWER = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))


def _entry(mode, cavity, control="Start&Setting"):
    return {
        "mode": mode,
        "version": "0101",
        "default": "Default",
        "control": control,
        "cavity": cavity,
        "tempMinC": "40",
        "tempMaxC": "250",
        "tempDefaultC": "160",
        "tempIntervalC": "5",
    }


SPEC = json.dumps(
    [
        _entry("Convection", "Single"),
        _entry("UpperConvection", "Upper"),
        _entry("LowerConvection", "Lower"),
        _entry("LowerBottom", "Lower"),
        _entry("LowerDrain", "Lower", control="Setting"),
    ]
)
MAIN_MODES = [
    "Convection",
    "UpperConvection",
    "LowerConvection",
    "LowerBottom",
    "LowerDrain",
    "HOMECARE_WIZARD_V2",
]


def _cavity(n: int, divider: str = "On") -> dict[str, dict]:
    def temps():
        return {
            "x.com.samsung.da.items": [
                {
                    "x.com.samsung.da.id": "0",
                    "x.com.samsung.da.current": "0",
                    "x.com.samsung.da.desired": "0",
                    "x.com.samsung.da.unit": "Celsius",
                    "x.com.samsung.da.increment": "5",
                }
            ]
        }

    return {
        f"/power/vs/{n}": {"x.com.samsung.da.power": "On"},
        f"/temperatures/vs/{n}": temps(),
        f"/oven/vs/{n}": {"x.com.samsung.da.state": "Ready"},
        f"/operational/state/vs/{n}": {
            "x.com.samsung.da.state": "Ready",
            "x.com.samsung.da.operationTime": "00:00:00",
            "x.com.samsung.da.remainingTime": "00:00:00",
            "x.com.samsung.da.progressPercentage": "0",
            "x.com.samsung.da.defaultTime": "01:00:00",
        },
        f"/connected/vs/{n}": {"x.com.samsung.da.connected": divider},
    }


def _resources(divider: str = "On", main_spec: str = SPEC, main_modes=MAIN_MODES) -> dict:
    return {
        "/information/vs/0": {"x.com.samsung.da.modelNum": "LCD_R18_OV_QMD_EU_22K|0|0|0"},
        "/remotectrl/vs/0": {"x.com.samsung.da.remoteControlEnabled": "true"},
        "/mode/vs/0": {
            "x.com.samsung.da.supportedModes": main_modes,
            "x.com.samsung.da.modes": ["NoOperation"],
            "x.com.samsung.da.defaultMode": "Convection",
            "x.com.samsung.da.modeSpec": main_spec,
        },
        **_cavity(0),
        "/mode/vs/1": {
            "x.com.samsung.da.supportedModes": ["HOMECARE_WIZARD_V2"],
            "x.com.samsung.da.modes": ["NoOperation"],
            "x.com.samsung.da.defaultMode": "Convection",
            "x.com.samsung.da.modeSpec": "[]",
        },
        **_cavity(1, divider),
    }


def _view(resources, subdevice):
    shared = cook.share_mode_spec(resources, [LOWER.to_actual])
    return canonical_view(subdevice, shared, [LOWER])


class TestShareModeSpec:
    def test_the_lower_zone_takes_the_lower_modes(self):
        lower = _view(_resources(), LOWER)

        assert cook.cavity(lower) == "Lower"
        assert cook.startable_modes(lower) == ["LowerConvection", "LowerBottom"]

    def test_the_upper_cavity_stops_offering_them(self):
        upper = _view(_resources(), MAIN)

        assert cook.cavity(upper) is None
        assert cook.startable_modes(upper) == ["Convection", "UpperConvection"]

    def test_none_while_the_divider_is_out(self):
        lower = _view(_resources(divider="Off"), LOWER)

        assert cook.cavity(lower) == "Lower"
        assert cook.startable_modes(lower) == []
        assert cook.can_start({}, lower)

    def test_a_lower_mode_starts_on_the_lower_zone_only(self):
        resources = _resources()

        plan = cook.plan_start(_view(resources, LOWER), mode="LowerConvection", duration=60)
        assert plan.batch(LOWER.to_actual)[0] == {
            "href": "/mode/vs/1",
            "rep": {"x.com.samsung.da.modes": ["LowerConvection"]},
        }
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_view(resources, MAIN), mode="LowerConvection", duration=60)
        assert err.value.key == "cook_mode_unavailable"

    def test_a_board_with_untagged_specs_is_unchanged(self):
        """The NE9801T's lower cavity lists its own Lower modes, so its view
        only gains the shared spec."""
        resources = _resources()
        resources["/mode/vs/1"] = {
            **resources["/mode/vs/1"],
            "x.com.samsung.da.supportedModes": ["LowerBottom"],
        }
        shared = cook.share_mode_spec(resources, [LOWER.to_actual])

        assert shared["/mode/vs/0"] == resources["/mode/vs/0"]
        assert shared["/mode/vs/1"]["x.com.samsung.da.supportedModes"] == ["LowerBottom"]

    def test_the_zone_is_found_when_the_main_oven_lists_no_lower_modes(self):
        """The tags alone mark the zone; the main oven's live list only
        narrows what it is offered."""
        main_modes = [m for m in MAIN_MODES if not m.startswith("Lower")]
        lower = _view(_resources(main_modes=main_modes), LOWER)

        assert cook.cavity(lower) == "Lower"
        assert cook.startable_modes(lower) == []

    def test_the_divider_out_select_offers_only_no_operation(self):
        lower = _view(_resources(divider="Off"), LOWER)

        assert oven._oven_mode_options(lower) == ["NoOperation"]

    def test_a_sibling_omitting_its_modes_is_not_zoned(self):
        resources = _resources()
        resources["/mode/vs/1"] = {
            k: v
            for k, v in resources["/mode/vs/1"].items()
            if k != "x.com.samsung.da.supportedModes"
        }
        shared = cook.share_mode_spec(resources, [LOWER.to_actual])

        assert shared["/mode/vs/0"] == resources["/mode/vs/0"]
        assert cook.cavity(canonical_view(LOWER, shared, [LOWER])) is None

    def test_the_divider_out_zone_refuses_a_mode_write(self):
        lower = _view(_resources(divider="Off"), LOWER)

        assert oven._oven_mode_write("Bake", lower["/mode/vs/0"], None, lower) is None

    def test_a_specless_cavity_without_supported_modes_offers_only_idle(self):
        resources = {"/mode/vs/0": {"x.com.samsung.da.defaultMode": "UpperConvection"}}

        assert oven._oven_mode_options(resources) == ["NoOperation"]


class TestCachedSpecs:
    def test_an_empty_spec_is_filled_from_the_cache(self):
        resources = _resources(main_spec="[]", main_modes=["HOMECARE_WIZARD_V2"])

        filled = cook.with_cached_specs(resources, {"/mode/vs/0": SPEC})

        assert filled["/mode/vs/0"]["x.com.samsung.da.modeSpec"] == SPEC
        # The live supportedModes still decides what can start.
        assert cook.startable_modes(filled) == []
        assert cook.can_start({}, filled)

    def test_a_reported_spec_wins(self):
        resources = _resources()

        assert cook.with_cached_specs(resources, {"/mode/vs/0": "[{}]"}) is resources

    def test_start_exists_while_the_board_answers_empty(self):
        """A first setup during #600's empty window, with nothing cached."""
        resources = _resources(main_spec="[]", main_modes=["HOMECARE_WIZARD_V2"])

        assert cook.can_start({}, resources)
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(resources, mode="Convection")
        assert err.value.key == "cook_start_not_supported"

    @pytest.mark.parametrize("spec", ["not json", "{}", {}])
    def test_a_malformed_spec_gets_no_start(self, spec):
        assert not cook.can_start({}, _resources(main_spec=spec))

    @pytest.mark.parametrize("spec", ["[ ]", "[\n]", []])
    def test_any_spelling_of_empty_gets_start(self, spec):
        assert cook.can_start({}, _resources(main_spec=spec))


def _discover(resources):
    bound, _name, materialized, _skipped = discover_partitioned(
        resources, [LOWER], resolve, CAPABILITIES, oic_device_types=("oic.d.oven",)
    )
    return bound, materialized


def _load(coord, resources):
    for href, rep in resources.items():
        coord._observe.apply(href, rep, source="poll")
    coord.bound, coord.subdevices = _discover(resources)


def _bound(coord, key, subdevice):
    return next(b for b in coord.bound if b.desc.key == key and b.subdevice == subdevice)


async def test_the_lower_zone_gets_its_entities(bare_coordinator) -> None:  # noqa: F811
    _load(bare_coordinator, _resources())

    assert bare_coordinator.subdevices == [LOWER]
    assert "Lower oven" in bare_coordinator.device_info_for(LOWER)["name"]
    for key in ("start_cooking", "divider"):
        bound = _bound(bare_coordinator, key, LOWER)
        view = bare_coordinator.discovery_canonical(LOWER)
        assert bound.desc.exists_fn(view.get(bound.href, {}), view)


async def test_the_lower_zone_starts_from_its_own_collection(bare_coordinator) -> None:  # noqa: F811
    _load(bare_coordinator, _resources())

    await bare_coordinator.async_send_command(
        _bound(bare_coordinator, "oven_mode", LOWER), "LowerConvection"
    )
    await bare_coordinator.async_send_command(_bound(bare_coordinator, "cook_time", LOWER), 1)
    await bare_coordinator.async_send_command(_bound(bare_coordinator, "start_cooking", LOWER), "")

    path, body = _posts(bare_coordinator)[-1]
    assert path == ["device", "1"]
    assert body == [
        {"href": "/mode/vs/1", "rep": {"x.com.samsung.da.modes": ["LowerConvection"]}},
        {
            "href": "/temperatures/vs/1",
            "rep": {
                "x.com.samsung.da.items": [
                    {
                        "x.com.samsung.da.id": "0",
                        "x.com.samsung.da.desired": "160",
                        "x.com.samsung.da.unit": "Celsius",
                    }
                ]
            },
        },
        {
            "href": "/operational/state/vs/1",
            "rep": {"x.com.samsung.da.operationTime": "00:01:00", "x.com.samsung.da.state": "Run"},
        },
    ]


async def test_the_main_oven_refuses_a_lower_mode(bare_coordinator) -> None:  # noqa: F811
    _load(bare_coordinator, _resources())

    with pytest.raises(ServiceValidationError) as err:
        await bare_coordinator.async_send_command(
            _bound(bare_coordinator, "oven_mode", MAIN), "LowerConvection"
        )

    assert err.value.translation_key == "cook_mode_unavailable"
    assert _posts(bare_coordinator) == []


async def test_a_lower_mode_changes_mid_cook(bare_coordinator) -> None:  # noqa: F811
    resources = _resources()
    resources["/mode/vs/1"] = {
        **resources["/mode/vs/1"],
        "x.com.samsung.da.modes": ["LowerConvection"],
    }
    resources["/operational/state/vs/1"] = {
        **resources["/operational/state/vs/1"],
        "x.com.samsung.da.state": "Run",
    }
    resources["/oven/vs/1"] = {"x.com.samsung.da.state": "Cooking"}
    _load(bare_coordinator, resources)

    await bare_coordinator.async_send_command(
        _bound(bare_coordinator, "oven_mode", LOWER), "LowerBottom"
    )

    assert _posts(bare_coordinator)[-1] == (
        ["mode", "vs", "1"],
        {"x.com.samsung.da.modes": ["LowerBottom"]},
    )


async def test_a_seen_spec_is_remembered(hass: HomeAssistant, bare_coordinator) -> None:  # noqa: F811
    _load(bare_coordinator, _resources())
    await hass.async_block_till_done()

    stored = await mode_spec_store(hass, bare_coordinator._entry).async_load()
    assert stored == {"/mode/vs/0": SPEC}

    # The Wi-Fi board restarts and answers empty until power is cut (#600).
    bare_coordinator._observe.apply(
        "/mode/vs/0",
        {
            "x.com.samsung.da.supportedModes": ["HOMECARE_WIZARD_V2"],
            "x.com.samsung.da.modeSpec": "[]",
        },
        source="poll",
    )
    await hass.async_block_till_done()
    assert await mode_spec_store(hass, bare_coordinator._entry).async_load() == stored

    fresh = type(bare_coordinator)(hass, bare_coordinator._entry)
    await fresh.async_load_mode_specs()
    for href, rep in bare_coordinator.last_resources.items():
        fresh._observe.apply(href, rep, source="poll")
    view = fresh.canonical_resources(MAIN)
    assert view["/mode/vs/0"]["x.com.samsung.da.modeSpec"] == SPEC
    assert cook.startable_modes(view) == []


async def test_diagnostics_report_the_remembered_spec(
    hass: HomeAssistant,
    enable_custom_integrations,
    bare_coordinator,  # noqa: F811
) -> None:
    _load(bare_coordinator, _resources())
    bare_coordinator._session = None
    diag = await async_get_config_entry_diagnostics(hass, bare_coordinator._entry)
    assert diag["remembered_mode_specs"] == {"/mode/vs/0": {"in_use": False, "spec": SPEC}}

    bare_coordinator._observe.apply(
        "/mode/vs/0", {"x.com.samsung.da.modeSpec": "[]"}, source="poll"
    )
    diag = await async_get_config_entry_diagnostics(hass, bare_coordinator._entry)
    assert diag["remembered_mode_specs"]["/mode/vs/0"]["in_use"]
    # `resources` stays what the device said.
    assert diag["resources"]["/mode/vs/0"]["x.com.samsung.da.modeSpec"] == "[]"


async def test_no_save_is_scheduled_after_close(hass: HomeAssistant, bare_coordinator) -> None:  # noqa: F811
    bare_coordinator._session = None
    await bare_coordinator.async_close()
    _load(bare_coordinator, _resources())
    await hass.async_block_till_done()

    assert await mode_spec_store(hass, bare_coordinator._entry).async_load() is None
