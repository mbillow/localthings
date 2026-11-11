"""Multi-indoor system-AC support behind an AIM-H04N Wi-Fi kit (issue #789).

The kit fronts several air-conditioner indoor units, each a UUID-prefixed
subdevice (issue #177). Two mapping problems were reported and are covered
here:

1. Temperature. The subdevices report current/target temperature through
   ``/temperatures/indoor/vs/0`` -- the same fields as ``/temperatures/vs/0``'s
   ``items[0]`` (current/desired/minimum/maximum/increment/unit) but *flat* on
   the resource, with no ``items[]`` wrapper -- and their ``/device/0`` batch
   omits the OCF-standard ``/temperature/current`` + ``/temperature/desired``
   pair. That href was on the AC ignore list (it is the EHS MIM-H04EN kit's
   fixed-24 placeholder, #682), so every unit's current/target temperature was
   silently ``None``.

2. Energy scale. The indoor units are airflow-legacy (``/airflow/vs/0``, no
   ``/wind/strength/vs/0``), so ``is_legacy_board`` selected the ARTIK051
   centiwatt-hour conversion (#193) and read lifetime energy ~100x low -- even
   though ``/energy/consumption/vs/0`` explicitly declares ``cumulativeUnit``
   ``"Wh"``, which ARTIK051 never does.

Temperature was verified on hardware (AIM-H04N, five indoor units): current
read, live setpoint updates from the wall remote, and setpoint writes from HA.
These stay HA-free like the rest of the suite -- registry, discovery/flatten
and the capability match/write contracts only.
"""

import json
from pathlib import Path

from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities.airconditioner import (
    ENERGY_METER_GENERIC,
    ENERGY_METER_LEGACY,
    _climate_write,
    _declares_plain_wh,
    _temperature_step,
    _temps_indoor_item,
)
from custom_components.localthings.registry.discovery import discover

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "airconditioner_aim_h04n_system_ac_device.json"
)


def _resources():
    return parse_device0_batch(json.loads(FIXTURE.read_text())["device0"])


def _registry(resources):
    # Routed by /oic/d device type: the indoor subdevice's modelNum is
    # SAC_SLIM1WAY, which has no board-token mapping, so oic.d.airconditioner
    # is the signal -- exactly how the coordinator resolves this subdevice.
    return resolve(resources, device_types=("oic.d.airconditioner",))


def test_subdevice_routes_to_airconditioner():
    reg = _registry(_resources())
    assert reg is not None and reg.name == "airconditioner"


def test_no_unbound_hrefs():
    """Every resource in the indoor-subdevice dump binds or is covered, so the
    coverage-gap repair stays clear."""
    resources = _resources()
    reg = _registry(resources)
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


# -- temperature -----------------------------------------------------------


def test_current_temperature_bound_from_indoor_vendor():
    """current_temperature_c comes from the flat /temperatures/indoor/vs/0 even
    though the OCF pair and the items[]-shaped /temperatures/vs/0 are absent."""
    resources = _resources()
    reg = _registry(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state.get("current_temperature_c") == 20.7


def test_temps_indoor_item_returns_flat_rep():
    rep = {
        "x.com.samsung.da.current": 20.7,
        "x.com.samsung.da.desired": 26.0,
        "x.com.samsung.da.unit": "Celsius",
    }
    assert _temps_indoor_item(rep) == rep


def test_temps_indoor_item_empty_without_a_reading():
    """A bare resource (link only, no temperature fields) yields {} so callers
    fall through cleanly rather than inventing a temperature."""
    assert _temps_indoor_item({"rt": ["x.com.samsung.da.temperatures"]}) == {}


def test_temps_indoor_item_desired_only_is_enough():
    assert _temps_indoor_item({"x.com.samsung.da.desired": 24.0}) == {
        "x.com.samsung.da.desired": 24.0
    }


def test_climate_write_targets_the_flat_indoor_resource():
    """A temperature_indoor write goes to /temperatures/indoor/vs/0 as a flat
    desired field (not /temperatures/vs/0's items[] array), quantized to the
    resource's own increment."""
    resources = _resources()
    segs, body = _climate_write(("temperature_indoor", 22), {}, resources=resources)
    assert segs == ["temperatures", "indoor", "vs", "0"]
    assert body == {"x.com.samsung.da.desired": "22"}


def test_temperature_step_reads_indoor_increment():
    assert _temperature_step(_resources()) == 1.0


# -- energy scale ----------------------------------------------------------


def test_declares_plain_wh():
    assert _declares_plain_wh({"x.com.samsung.da.cumulativeUnit": "Wh"}) is True
    assert _declares_plain_wh({"x.com.samsung.da.cumulativeUnit": "wh"}) is True
    # ARTIK051 (#193) omits the unit field, and a KW *instantaneous* unit is a
    # different field that must not be mistaken for the cumulative one.
    assert _declares_plain_wh({}) is False
    assert _declares_plain_wh({"x.com.samsung.da.instantaneousPowerUnit": "KW"}) is False


def test_energy_meter_selects_wh_scale_when_declared():
    """The AIM-H04N indoor unit is airflow-legacy but declares Wh, so the
    generic Wh->kWh meter claims it and the centiwatt-hour meter does not."""
    resources = _resources()
    rep = resources["/energy/consumption/vs/0"]
    assert ENERGY_METER_GENERIC.match_fn is not None
    assert ENERGY_METER_LEGACY.match_fn is not None
    assert ENERGY_METER_GENERIC.match_fn(rep, resources) is True
    assert ENERGY_METER_LEGACY.match_fn(rep, resources) is False


def test_energy_meter_stays_legacy_without_a_declared_unit():
    """An airflow-legacy board that declares no unit (ARTIK051, #193) keeps the
    centiwatt-hour meter -- the fix must not change its scale."""
    resources = {
        "/airflow/vs/0": {"x.com.samsung.da.speedLevel": "3"},
        "/energy/consumption/vs/0": {"x.com.samsung.da.cumulativePower": "275239500"},
    }
    rep = resources["/energy/consumption/vs/0"]
    assert ENERGY_METER_LEGACY.match_fn is not None
    assert ENERGY_METER_GENERIC.match_fn is not None
    assert ENERGY_METER_LEGACY.match_fn(rep, resources) is True
    assert ENERGY_METER_GENERIC.match_fn(rep, resources) is False


def test_wh_scale_corrects_the_hundredfold_underread():
    """The declared-Wh meter divides by 1000 (1,771,403 Wh -> 1771.4 kWh); the
    centiwatt-hour meter would read ~100x low (17.71 kWh)."""
    generic = next(e for e in ENERGY_METER_GENERIC.entities if e.key == "energy_kwh")
    legacy = next(e for e in ENERGY_METER_LEGACY.entities if e.key == "energy_kwh")
    assert generic.value_fn("1771403.0") == 1771.4
    assert legacy.value_fn("1771403.0") == 17.71
