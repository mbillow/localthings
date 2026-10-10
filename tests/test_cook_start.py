"""Starting a cook from the board's own modeSpec (issue #473): what each
board declares startable, the checks a start must pass, the Collection
payload it sends, and the choices held while the oven is idle."""

import json
import re
from pathlib import Path

import pytest

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import cook, microwave, oven
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.subdevices import canonical_view
from tests.conftest import _discover_full, _load_device, _load_device_full

_TRANSLATIONS = Path(__file__).parent.parent / "custom_components/localthings/translations"


def _idle(name):
    """The dump with nothing cooking. #473's was taken in Keep Warm; its
    own pre-start readback in the issue shows NoOperation and desired 0."""
    resources = _load_device(name)
    resources["/mode/vs/0"] = {**resources["/mode/vs/0"], "x.com.samsung.da.modes": ["NoOperation"]}
    items = [dict(i) for i in resources["/temperatures/vs/0"]["x.com.samsung.da.items"]]
    items[0]["x.com.samsung.da.desired"] = "0"
    resources["/temperatures/vs/0"] = {
        **resources["/temperatures/vs/0"],
        "x.com.samsung.da.items": items,
    }
    return resources


def _running(resources):
    return {
        **resources,
        "/operational/state/vs/0": {
            **resources["/operational/state/vs/0"],
            "x.com.samsung.da.state": "Run",
        },
    }


@pytest.mark.parametrize(
    ("name", "startable"),
    [
        ("range_ne63a6111ss", ["Bake"]),
        (
            "range_ne6516a",
            ["Bake", "ConvectionBake", "ConvectionRoast", "AirFryer", "Dehydrate"],
        ),
        # The upper cavity of a dual-cavity range: its modeSpec declares
        # 13 startable modes across both cavities and the whole oven, but it
        # supports only the Upper ones now, and UpperBroil is Setting-only.
        ("range_tp1x_da_ks_range_0101x", ["UpperConvectionBake", "UpperConvectionRoast"]),
        # Gas: every mode is Setting-only, and its manual forbids a remote start.
        ("range_nx60t8311ss", []),
        # No MicroWave* mode is startable.
        ("microwave_mw7300b", ["Convection", "AirFryer", "Grill", "Deodorization"]),
        ("qooker_mw7500a", []),
        # No modeSpec: the board's own live modes, less maintenance programs
        # and the modes no modeSpec ever declares startable (#300).
        (
            "oven",
            [
                "Convection",
                "TopHeatPluseConvection",
                "Conventional",
                "LargeGrill",
                "SmallGrill",
                "BottomHeatPluseConvection",
                "PlateWarm",
                "KeepWarm",
                "Bottom",
                "EcoConvection",
                "FanGrill",
            ],
        ),
        (
            "oven_tp2x_ks_walloven",
            ["ConvectionBake", "ConvectionRoast", "Bake", "SteamBake", "SteamRoast"],
        ),
        ("microwave_me7500d", ["KeepWarm"]),
        ("microwave_nw9300md", ["Convection"]),
        ("oven_nv75n_dual_cook", []),
    ],
)
def test_startable_modes_come_from_the_boards_own_declaration(name, startable):
    assert cook.startable_modes(_load_device(name)) == startable


class TestPlan:
    def test_the_measured_473_start(self):
        """The exact payload that started #473's range, from the mode,
        setpoint and time given."""
        plan = cook.plan_start(
            _idle("range_ne63a6111ss"), mode="Bake", temperature=350, duration=600
        )

        assert plan.batch() == [
            {"href": "/devices/0"},
            {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Bake"]}},
            {
                "href": "/temperatures/vs/0",
                "rep": {
                    "x.com.samsung.da.items": [
                        {
                            "x.com.samsung.da.id": "0",
                            "x.com.samsung.da.desired": "350",
                            "x.com.samsung.da.unit": "Fahrenheit",
                        }
                    ]
                },
            },
            {
                "href": "/operational/state/vs/0",
                "rep": {
                    "x.com.samsung.da.operationTime": "00:10:00",
                    "x.com.samsung.da.state": "Run",
                },
            },
        ]

    def test_what_is_left_out_comes_from_the_mode(self):
        plan = cook.plan_start(_idle("range_ne63a6111ss"))

        assert plan == cook.CookPlan(mode="Bake", temperature=350, unit="Fahrenheit", duration=3600)

    def test_temperature_rounds_to_the_step(self):
        # The 473 board's modeSpec declares no interval; its temperatures
        # item's increment is 5.
        plan = cook.plan_start(_idle("range_ne63a6111ss"), mode="Bake", temperature=352)

        assert plan.temperature == 350

    def test_a_mode_with_no_temperature_sends_none(self):
        plan = cook.plan_start(_load_device("microwave_mw7300b"), mode="Grill")

        assert plan.temperature is None
        assert "/temperatures/vs/0" not in [e["href"] for e in plan.batch()]

    @pytest.mark.parametrize(
        ("kwargs", "key", "placeholders"),
        [
            (
                {"mode": "Broil"},
                "cook_mode_not_startable",
                {"mode": "Broil", "startable": "Bake"},
            ),
            ({"mode": "Bake", "temperature": 600}, "cook_temperature_out_of_range", None),
            ({"mode": "Bake", "duration": 30}, "cook_duration_out_of_range", None),
        ],
    )
    def test_rejections_name_what_the_board_allows(self, kwargs, key, placeholders):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_idle("range_ne63a6111ss"), **kwargs)

        assert err.value.key == key
        if placeholders is not None:
            assert err.value.placeholders == placeholders

    def test_out_of_range_names_the_limits(self):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_idle("range_ne63a6111ss"), mode="Bake", temperature=600)

        assert err.value.placeholders == {"mode": "Bake", "min": "175°F", "max": "500°F"}

    @pytest.mark.parametrize("mode", ["LowerBake", "Bake"])
    def test_a_startable_mode_this_cavity_does_not_offer_is_refused(self, mode):
        """The lower cavity's mode, and a whole-oven one while the upper
        cavity offers only its own."""
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_load_device("range_tp1x_da_ks_range_0101x"), mode=mode)

        assert err.value.key == "cook_mode_unavailable"
        assert err.value.placeholders["startable"] == "UpperConvectionBake, UpperConvectionRoast"

    def test_a_temperature_for_a_mode_without_one_is_refused(self):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_load_device("microwave_mw7300b"), mode="Grill", temperature=200)

        assert err.value.key == "cook_temperature_not_supported"

    def test_no_mode_and_no_startable_default(self):
        resources = _idle("range_ne63a6111ss")
        resources["/mode/vs/0"] = {
            **resources["/mode/vs/0"],
            "x.com.samsung.da.defaultMode": "Broil",
        }

        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(resources)

        assert err.value.key == "cook_mode_required"

    @pytest.mark.parametrize("name", ["range_nx60t8311ss", "qooker_mw7500a"])
    def test_a_board_that_declares_nothing_startable_is_refused(self, name):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_load_device(name), mode="Bake")

        assert err.value.key == "cook_start_not_supported"

    def test_a_running_oven_is_refused(self):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_running(_idle("range_ne63a6111ss")), mode="Bake")

        assert err.value.key == "cook_start_not_idle"

    def test_keep_warm_under_ready_is_not_idle(self):
        """#473's dump as captured: Keep Warm at 175F with the operational
        state and the cavity both reading Ready."""
        resources = _load_device("range_ne63a6111ss")

        assert not cook.is_idle(resources)
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(resources, mode="Bake")
        assert err.value.key == "cook_start_not_idle"


def _lower_cavity():
    """The NE9801T's lower cavity as the coordinator sees it: its own
    canonical view, with the upper cavity's modeSpec shared."""
    resources, oic_res, seeds = _load_device_full("range_tp1x_da_ks_range_0101x")
    _bound, subdevices, _skipped, full, _name = _discover_full(
        resources, oic_res, seeds, ("oic.wk.d", "oic.d.range")
    )
    (lower,) = subdevices
    shared = cook.share_mode_spec(full, [lower.to_actual(cook.MODE_HREF)])
    return lower, canonical_view(lower, shared, subdevices)


class TestLowerCavity:
    def test_it_starts_its_own_modes(self):
        _lower, resources = _lower_cavity()

        assert cook.startable_modes(resources) == ["LowerBake", "LowerConvectionBake"]

    def test_without_the_shared_modespec_it_loses_the_declared_limits(self):
        """Unshared, the lower cavity falls back to its own live modes with
        the static setpoint range instead of the board's declared one."""
        lower, shared = _lower_cavity()
        resources, oic_res, seeds = _load_device_full("range_tp1x_da_ks_range_0101x")
        _bound, subdevices, _skipped, full, _name = _discover_full(
            resources, oic_res, seeds, ("oic.wk.d", "oic.d.range")
        )
        unshared = canonical_view(lower, full, subdevices)

        assert cook.mode_specs(unshared) != cook.mode_specs(shared)
        assert cook.temp_bounds(shared, "LowerBake") != cook.temp_bounds(unshared, "LowerBake")

    def test_its_batch_uses_its_own_hrefs_and_no_marker(self):
        lower, resources = _lower_cavity()

        plan = cook.plan_start(resources)
        batch = plan.batch(lower.to_actual)

        assert plan.mode == "LowerConvectionBake"
        assert [e["href"] for e in batch] == [
            "/mode/vs/1",
            "/temperatures/vs/1",
            "/operational/state/vs/1",
        ]

    def test_the_upper_cavitys_mode_is_refused(self):
        _lower, resources = _lower_cavity()

        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(resources, mode="UpperConvectionBake")

        assert err.value.key == "cook_mode_unavailable"


class TestBounds:
    def test_a_modes_own_range(self):
        resources = _idle("range_ne63a6111ss")

        assert cook.temp_bounds(resources, "Bake") == (175, 500, 5)

    def test_broils_codes_are_not_a_range(self):
        assert cook.temp_bounds(_idle("range_ne63a6111ss"), "Broil") is None

    def test_the_setpoint_follows_the_held_mode(self):
        resources = _idle("range_ne6516a")
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "AirFryer", resources)

        view = held.overlay(resources)

        assert oven._mode_setpoint_bounds({}, view) == (350.0, 500.0, 1.0)
        assert oven._cook_time_bounds({}, view) == (1.0, 599.0, 1.0)

    def test_cook_time_keeps_its_static_range_mid_cook(self):
        assert oven._cook_time_bounds({}, _running(_idle("range_ne6516a"))) is None


class TestHeld:
    def test_a_mode_brings_its_own_defaults(self):
        resources = _idle("range_ne6516a")
        held = cook.HeldCook()

        held.hold(cook.PARAM_MODE, "AirFryer", resources)
        view = held.overlay(resources)

        assert view["/mode/vs/0"]["x.com.samsung.da.modes"] == ["AirFryer"]
        assert (
            view["/temperatures/vs/0"]["x.com.samsung.da.items"][0]["x.com.samsung.da.desired"]
            == "425"
        )
        assert view["/operational/state/vs/0"]["x.com.samsung.da.operationTime"] == "01:00:00"
        # The device's own snapshot is untouched.
        assert resources["/mode/vs/0"]["x.com.samsung.da.modes"] == ["NoOperation"]

    def test_the_start_sends_what_is_held(self):
        resources = _idle("range_ne6516a")
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "AirFryer", resources)
        held.hold(cook.PARAM_TEMPERATURE, 400, resources)
        held.hold(cook.PARAM_DURATION, 1200, resources)

        assert held.plan(resources) == cook.CookPlan(
            mode="AirFryer", temperature=400, unit="Fahrenheit", duration=1200
        )

    def test_a_new_mode_drops_the_held_setpoint_and_time(self):
        resources = _idle("range_ne6516a")
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "AirFryer", resources)
        held.hold(cook.PARAM_TEMPERATURE, 400, resources)

        held.hold(cook.PARAM_MODE, "Bake", resources)

        assert held.values == {cook.PARAM_MODE: "Bake"}

    def test_a_setpoint_alone_uses_the_default_mode(self):
        resources = _idle("range_ne63a6111ss")
        held = cook.HeldCook()

        held.hold(cook.PARAM_TEMPERATURE, 400, resources)

        assert held.overlay(resources)["/mode/vs/0"]["x.com.samsung.da.modes"] == ["Bake"]
        assert held.plan(resources).mode == "Bake"

    def test_a_mode_the_board_cannot_start_is_not_held(self):
        resources = _idle("range_ne63a6111ss")
        held = cook.HeldCook()

        with pytest.raises(cook.CookStartError) as err:
            held.hold(cook.PARAM_MODE, "KeepWarm", resources)

        assert err.value.key == "cook_mode_not_startable"
        assert held.values == {}

    def test_an_out_of_range_setpoint_is_not_held(self):
        resources = _idle("range_ne63a6111ss")
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "Bake", resources)

        with pytest.raises(cook.CookStartError):
            held.hold(cook.PARAM_TEMPERATURE, 100, resources)

        assert held.values == {cook.PARAM_MODE: "Bake"}

    def test_each_held_value_is_checked_with_the_others(self):
        """A mode with no default time: a held temperature must not be
        checked against a default that isn't there once a time is held."""
        resources = _idle("range_ne63a6111ss")
        spec = json.loads(resources["/mode/vs/0"]["x.com.samsung.da.modeSpec"])
        spec[0]["timeDefault"] = "NotSupported"
        resources["/mode/vs/0"] = {
            **resources["/mode/vs/0"],
            "x.com.samsung.da.modeSpec": json.dumps(spec),
        }
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "Bake", resources)

        held.hold(cook.PARAM_DURATION, 1800, resources)
        held.hold(cook.PARAM_TEMPERATURE, 400, resources)

        assert held.plan(resources) == cook.CookPlan(
            mode="Bake", temperature=400, unit="Fahrenheit", duration=1800
        )

    def test_the_panel_turning_on_drops_everything(self):
        resources = _idle("range_ne63a6111ss")
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "Bake", resources)

        started = _running(resources)

        assert held.overlay(started) is started
        assert held.values == {}

    def test_a_setting_changed_at_the_panel_drops_everything(self):
        resources = _idle("range_ne63a6111ss")
        held = cook.HeldCook()
        held.hold(cook.PARAM_TEMPERATURE, 400, resources)
        items = [dict(i) for i in resources["/temperatures/vs/0"]["x.com.samsung.da.items"]]
        items[0]["x.com.samsung.da.desired"] = "325"
        changed = {**resources, "/temperatures/vs/0": {"x.com.samsung.da.items": items}}

        held.overlay(changed)

        assert held.values == {}


class TestEntities:
    def _bound(self, name):
        resources = _idle(name)
        reg = resolve(resources)
        assert reg is not None
        return discover(resources, reg.capabilities, reg.pattern_capabilities), resources

    def test_the_start_button_binds_where_a_mode_can_start(self):
        bound, resources = self._bound("range_ne63a6111ss")
        start = next(b for b in bound if b.desc.key == "start_cooking")

        assert start.desc.exists_fn is cook.can_start
        assert cook.can_start(resources.get(start.href) or {}, resources)

    @pytest.mark.parametrize("name", ["range_nx60t8311ss", "qooker_mw7500a"])
    def test_the_start_button_stays_away_elsewhere(self, name):
        resources = _load_device(name)
        reg = resolve(resources)
        assert reg is not None
        bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
        start = next(b for b in bound if b.desc.key == "start_cooking")

        assert start.desc.exists_fn is cook.can_start
        assert not cook.can_start(resources.get(start.href) or {}, resources)

    def test_held_values_read_back_through_the_entities(self):
        bound, resources = self._bound("range_ne63a6111ss")
        held = cook.HeldCook()
        held.hold(cook.PARAM_MODE, "Bake", resources)
        held.hold(cook.PARAM_DURATION, 1800, resources)

        state = flatten(bound, held.overlay(resources))

        assert state["oven_mode"] == "Bake"
        assert state["oven_setpoint"] == 350
        assert state["cook_time"] == 30


def test_every_start_rejection_is_translated():
    source = Path(cook.__file__).read_text()
    keys = set(re.findall(r'CookStartError\(\s*"(\w+)"', source))
    assert keys
    for path in sorted(_TRANSLATIONS.glob("*.json")):
        exceptions = json.loads(path.read_text())["exceptions"]
        assert keys <= set(exceptions), (path.name, sorted(keys - set(exceptions)))


class TestWithoutModeSpec:
    """#300: the NW9000KD publishes no modeSpec and started Bake from Ready
    with the same Collection write as every other board."""

    def test_the_measured_300_start(self):
        plan = cook.plan_start(
            _idle("oven_tp2x_ks_walloven"), mode="Bake", temperature=350, duration=600
        )

        assert plan.batch() == [
            {"href": "/devices/0"},
            {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Bake"]}},
            {
                "href": "/temperatures/vs/0",
                "rep": {
                    "x.com.samsung.da.items": [
                        {
                            "x.com.samsung.da.id": "0",
                            "x.com.samsung.da.desired": "350",
                            "x.com.samsung.da.unit": "Fahrenheit",
                        }
                    ]
                },
            },
            {
                "href": "/operational/state/vs/0",
                "rep": {
                    "x.com.samsung.da.operationTime": "00:10:00",
                    "x.com.samsung.da.state": "Run",
                },
            },
        ]

    def test_cook_time_is_optional(self):
        plan = cook.plan_start(_idle("oven_tp2x_ks_walloven"), mode="Bake", temperature=350)
        assert plan.duration is None
        assert plan.batch()[-1] == {
            "href": "/operational/state/vs/0",
            "rep": {"x.com.samsung.da.operationTime": "00:00:00", "x.com.samsung.da.state": "Run"},
        }

    def test_temperature_is_bounded_by_the_static_setpoint_range(self):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_idle("oven_tp2x_ks_walloven"), mode="Bake", temperature=600)
        assert err.value.key == "cook_temperature_out_of_range"
        assert cook.temp_bounds(_idle("oven_tp2x_ks_walloven"), "Bake") == (
            oven.SETPOINT_MIN_F,
            oven.SETPOINT_MAX_F,
            oven.SETPOINT_STEP_F,
        )

    def test_a_maintenance_program_is_not_startable(self):
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(_idle("oven_tp2x_ks_walloven"), mode="Descale")
        assert err.value.key == "cook_mode_not_startable"

    def test_a_microwave_keeps_its_own_range(self):
        """The microwave family's static range is Celsius only, so a
        Fahrenheit microwave offers no temperature rather than the oven's."""
        resources = _load_device("microwave_nw9300md")
        resources["/mode/vs/0"] = {
            **resources["/mode/vs/0"],
            "x.com.samsung.da.modes": ["NoOperation"],
        }
        assert cook.device_unit(resources) == "Fahrenheit"
        assert cook.temp_bounds(resources, "Convection") is None
        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(resources, mode="Convection", temperature=550)
        assert err.value.key == "cook_temperature_not_supported"

        items = [dict(i) for i in resources["/temperatures/vs/0"]["x.com.samsung.da.items"]]
        items[0]["x.com.samsung.da.unit"] = "Celsius"
        resources["/temperatures/vs/0"] = {
            **resources["/temperatures/vs/0"],
            "x.com.samsung.da.items": items,
        }
        assert cook.temp_bounds(resources, "Convection") == (
            microwave.SETPOINT_MIN_C,
            microwave.SETPOINT_MAX_C,
            microwave.SETPOINT_STEP_C,
        )


def _lcd_r18_microwave():
    """#600's NQ5B6753CAA idle, with its Convection modeSpec entry verbatim:
    every LCD_R18 entry leaves the time keys out."""
    entry = {
        "mode": "Convection",
        "version": "0101",
        "default": "Default",
        "control": "Start&Setting",
        "cavity": "Single",
        "tempMinC": "40",
        "tempMaxC": "250",
        "tempDefaultC": "160",
        "tempListLengthC": "0",
        "tempMinF": "NotSupported",
        "tempMaxF": "NotSupported",
        "tempDefaultF": "NotSupported",
        "tempListLengthF": "0",
        "probeMinC": "NotSupported",
        "probeMaxC": "NotSupported",
        "probeDefaultC": "NotSupported",
        "probeMinF": "NotSupported",
        "probeMaxF": "NotSupported",
        "probeDefaultF": "NotSupported",
        "powerDefault": "NotSupported",
        "powerListLength": "0",
        "tempIntervalC": "5",
        "tempIntervalF": "NotSupported",
        "probeIntervalC": "NotSupported",
        "probeIntervalF": "NotSupported",
    }
    return {
        "/mode/vs/0": {
            "x.com.samsung.da.modes": ["NoOperation"],
            "x.com.samsung.da.supportedModes": ["Convection", "HOMECARE_WIZARD_V2"],
            "x.com.samsung.da.modeSpec": json.dumps([entry]),
        },
        "/temperatures/vs/0": {
            "x.com.samsung.da.items": [
                {
                    "x.com.samsung.da.id": "0",
                    "x.com.samsung.da.desired": "0",
                    "x.com.samsung.da.unit": "Celsius",
                }
            ]
        },
        "/operational/state/vs/0": {"x.com.samsung.da.state": "Ready"},
    }


class TestUndeclaredCookTime:
    """#600: an LCD_R18 board ignored a start that carried no operationTime,
    and its modeSpec has no time keys, so every cook time was refused."""

    def test_the_measured_600_start(self):
        plan = cook.plan_start(
            _lcd_r18_microwave(), mode="Convection", temperature=160, duration=60
        )

        assert plan.batch() == [
            {"href": "/devices/0"},
            {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Convection"]}},
            {
                "href": "/temperatures/vs/0",
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
                "href": "/operational/state/vs/0",
                "rep": {
                    "x.com.samsung.da.operationTime": "00:01:00",
                    "x.com.samsung.da.state": "Run",
                },
            },
        ]

    def test_a_start_without_a_cook_time_sends_none(self):
        plan = cook.plan_start(_lcd_r18_microwave(), mode="Convection", temperature=160)

        assert plan.duration is None
        assert plan.batch()[-1]["rep"]["x.com.samsung.da.operationTime"] == "00:00:00"

    def test_a_cook_time_can_be_held_before_the_rest(self):
        plan = cook.plan_start(
            _lcd_r18_microwave(), mode="Convection", duration=300, complete=False
        )

        assert plan.duration == 300

    def test_not_supported_still_means_no_timer(self):
        """Other boards write NotSupported for a mode with no timer."""
        resources = _lcd_r18_microwave()
        entry = json.loads(resources["/mode/vs/0"]["x.com.samsung.da.modeSpec"])[0]
        entry.update(timeMin="NotSupported", timeMax="NotSupported", timeDefault="NotSupported")
        resources["/mode/vs/0"]["x.com.samsung.da.modeSpec"] = json.dumps([entry])

        with pytest.raises(cook.CookStartError) as err:
            cook.plan_start(resources, mode="Convection", duration=60)

        assert err.value.key == "cook_duration_not_supported"
