"""TP6X_VTL_16K air purifier over the 8888 bridge (issue #608).

Its power and speedLevel writes were confirmed on the unit, each read back
and checked against the purifier itself: `{"Device": {"Operation": {"power":
"On"}}}` and `{"Device": {"Wind": {"speedLevel": 3}}}`, with speedLevel the
same Auto=0, Sleep=1, Low=2, Medium=3, High=4 code as the TVTL's /airflow/0.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.fan import LocalThingsAirflowFan
from custom_components.localthings.legacy_http import table_for, to_resources, to_write
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities.air_purifier import (
    AIRFLOW_LEVEL_FAN,
)
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import FanDesc

_FIXTURE = Path(__file__).parent / "fixtures" / "air_purifier_tp6x_vtl_16k_8888.json"
_TABLE = table_for("Air_Purifier")


def _resources(**wind) -> dict[str, dict]:
    bodies = json.loads(_FIXTURE.read_text(encoding="utf-8"))["bodies"]
    if wind:
        bodies["Wind"] = {**bodies["Wind"], **wind}
    return to_resources(bodies, _TABLE)


def _bound(resources):
    registry = resolve(resources)
    assert registry is not None
    unbound: list[str] = []
    bound = discover(
        resources, registry.capabilities, registry.pattern_capabilities, log=unbound.append
    )
    return registry, bound, unbound


def test_routes_to_the_air_purifier_and_binds_every_resource():
    registry, bound, unbound = _bound(_resources())
    assert registry.name == "air_purifier"
    assert unbound == []
    assert any(b.capability is AIRFLOW_LEVEL_FAN for b in bound)


def test_reads_air_quality_and_filter():
    resources = _resources()
    _, bound, _ = _bound(resources)
    state = flatten(bound, resources)
    assert state["dust"] == 9
    assert state["fine_dust"] == 9
    assert state["odor"] == 1
    assert state["clean_level"] == 1
    assert state["filter_progress"] == 3
    assert "fan_speed_level" not in state


def test_region_only_configuration_does_not_read_as_remote_control_off():
    """It held only `region`, and on /remotectrl/vs/0 the missing
    remoteControlEnabled read as Smart Control off, blocking every write."""
    resources = _resources()
    assert "/remotectrl/vs/0" not in resources
    assert resources["/configuration/vs/0"] == {"x.com.samsung.da.region": "000"}


class _FakeCoordinator:
    device_key = "TEST-VTL"
    device_info: dict = {}  # noqa: RUF012
    data: dict = {}  # noqa: RUF012

    def __init__(self, resources):
        self.last_resources = resources
        self.commands: list = []

    def resource(self, href):
        return self.last_resources.get(href, {})

    def canonical_resources(self, subdevice):
        return self.last_resources

    async def async_send_command(self, bound, payload):
        self.commands.append((bound, payload))


def _fan(resources):
    _, bound, _ = _bound(resources)
    fan_bound = next(b for b in bound if isinstance(b.desc, FanDesc))
    coordinator = _FakeCoordinator(resources)
    return LocalThingsAirflowFan(cast(LocalThingsCoordinator, coordinator), fan_bound), coordinator


def test_fan_reads_presets_and_speeds():
    fan, _ = _fan(_resources())
    assert fan.is_on is True
    assert fan.preset_mode == "auto"
    fan, _ = _fan(_resources(speedLevel=1))
    assert fan.preset_mode == "sleep"
    fan, _ = _fan(_resources(speedLevel=4))
    assert fan.preset_mode is None
    assert fan.percentage == 100


async def test_fan_writes_reach_the_confirmed_wire_bodies():
    fan, coordinator = _fan(_resources())
    await fan.async_set_percentage(67)
    await fan.async_turn_off()

    wire = []
    for bound, payload in coordinator.commands:
        segs, body = bound.desc.write_fn(payload, {}, bound.href)
        wire.append(to_write([("/" + "/".join(segs), body)], _TABLE))
    assert wire == [
        {"Device": {"Wind": {"speedLevel": 3}}},
        {"Device": {"Operation": {"power": "Off"}}},
    ]
