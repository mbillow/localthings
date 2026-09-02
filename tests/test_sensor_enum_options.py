"""An enum sensor's reported state must always be inside its options.

Home Assistant raises for an enum sensor whose state isn't in `options`
(sensor/__init__.py: "provides state value ... which is not in the list of
options provided"), so a value outside the list isn't a cosmetic problem --
it takes the entity out.

Two ways that bites, both from PR #341 giving `progress` a `device_class`
of enum:

- the sticky hold (issue #345) froze the entity at the device's raw
  'Finish' while `rep_fn` had been normalized to 'finish', so every
  completed cycle -- the exact path #345 exists to serve -- produced a
  state outside the options;
- any progress value not in the translation catalog. Every token the
  shipped fixtures advertise is covered today, but this registry's rule is
  that an unrecognized device value renders raw rather than breaking, and
  Samsung ships more devices than we have dumps for.
"""

from __future__ import annotations

from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.capabilities.operational import OPERATIONAL_STATE
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import SensorDesc
from custom_components.localthings.sensor import LocalThingsSensor

_HREF = "/operational/state/vs/0"
_PROGRESS = next(
    e for e in OPERATIONAL_STATE.entities if e.key == "progress" and isinstance(e, SensorDesc)
)
_ALL_BOUND = [
    BoundEntity(href=_HREF, capability=OPERATIONAL_STATE, desc=desc)
    for desc in OPERATIONAL_STATE.entities
]


class _FakeConfigEntry:
    def __init__(self):
        self.options: dict = {}


class _FakeCoordinator:
    def __init__(self):
        self.device_key = "TEST-SERIAL"
        self.config_entry = _FakeConfigEntry()
        self.resources: dict[str, dict] = {}
        self.bound = _ALL_BOUND

    def resource(self, href: str) -> dict:
        return self.resources.get(href) or {}

    def canonical_resources(self, _subdevice) -> dict[str, dict]:
        return self.resources

    @property
    def data(self) -> dict:
        return flatten(self.bound, self.resources)


def _sensor(desc):
    coordinator = _FakeCoordinator()
    bound = BoundEntity(href=_HREF, capability=OPERATIONAL_STATE, desc=desc)
    coordinator.bound = [bound]
    return LocalThingsSensor(cast(LocalThingsCoordinator, coordinator), bound), coordinator


def _set(coordinator, **fields):
    coordinator.resources[_HREF] = {f"x.com.samsung.da.{k}": v for k, v in fields.items()}


def test_the_sticky_hold_freezes_at_a_value_inside_the_options():
    """Issue #345's grace window fires on every finished cycle, so a held
    value outside the options would break the common path, not an edge."""
    sensor, coordinator = _sensor(_PROGRESS)

    _set(coordinator, state="Run", progress="Wash")
    assert sensor.native_value == "wash"

    # Cycle finishes, then the device drops out of active -- the hold engages.
    _set(coordinator, state="Run", progress="Finish")
    assert sensor.native_value in sensor.options
    _set(coordinator, state="Ready", progress="Finish")
    held = sensor.native_value
    assert held == "finish"
    assert held in sensor.options


def test_a_progress_value_we_cannot_translate_still_reports():
    """An unrecognized device value renders raw rather than taking the
    entity out -- the same rule the course tables follow."""
    sensor, coordinator = _sensor(_PROGRESS)

    _set(coordinator, state="Run", progress="SomeFutureStage")
    value = sensor.native_value
    assert value == "somefuturestage"
    assert value in sensor.options
    # ...and admitting it doesn't drop the translated ones.
    assert "rinse" in sensor.options


def test_known_values_do_not_grow_the_options_list():
    assert _PROGRESS.options is not None
    sensor, coordinator = _sensor(_PROGRESS)

    _set(coordinator, state="Run", progress="Rinse")
    assert sensor.options == list(_PROGRESS.options)


def test_callable_options_follow_the_live_resource_snapshot():
    desc = SensorDesc(
        key="sense_level",
        field="desiredSenseLevel",
        device_class="enum",
        options=lambda resources: list(resources[_HREF]["supportedSenseLevels"]),
    )
    sensor, coordinator = _sensor(desc)

    coordinator.resources[_HREF] = {
        "desiredSenseLevel": "near",
        "supportedSenseLevels": ["near", "far"],
    }
    assert sensor.options == ["near", "far"]

    # A newly reported value remains displayable even before the advertised
    # list catches up, preserving the existing enum-sensor safety behavior.
    coordinator.resources[_HREF] = {
        "desiredSenseLevel": "middle",
        "supportedSenseLevels": ["near", "far"],
    }
    assert sensor.options == ["near", "far", "middle"]


def test_a_non_enum_sensor_has_no_options():
    percentage = next(e for e in OPERATIONAL_STATE.entities if e.key == "progress_percentage")
    sensor, coordinator = _sensor(percentage)

    _set(coordinator, state="Run", progressPercentage="40")
    assert sensor.options is None
    assert sensor.native_value == 40
