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
    _AC_IGNORED,
    ENERGY_METER_GENERIC,
    ENERGY_METER_LEGACY,
    _climate_write,
    _declares_plain_wh,
    _temperature_step,
    _temps_indoor_item,
)
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.subdevices import canonical_view

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "airconditioner_aim_h04n_system_ac_device.json"
)

# The whole-kit capture: the master Wi-Fi kit plus its five UUID-prefixed indoor
# subdevices (the single fixture above is one indoor subdevice on its own).
KIT = "airconditioner_aim_h04n_system_ac_kit"
_KIT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / f"{KIT}_device.json"


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


def test_outdoor_temperature_bound_from_outside_vendor():
    """outdoor_temperature comes from /temperatures/outside/vs/0 on each indoor subdevice."""
    resources = _resources()
    reg = _registry(resources)
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    state = flatten(bound, resources)
    assert state.get("outdoor_temperature") == 10.4


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


# -- whole kit: five indoor subdevices behind one Wi-Fi kit -----------------
#
# A real diagnostics capture: the AIM-H04N master plus five UUID-prefixed indoor
# subdevices from its subdeviceIdList (Pattern B), run through the
# subdevice-aware pipeline (enumerate_subdevices + discover_partitioned).


def _kit():
    """Run the whole subdevice-aware pipeline against the kit fixture, HA-free.

    Returns (bound, materialized, skipped, full_resources)."""
    from tests.conftest import _discover_full, _load_device_full

    resources, oic_res, seeds = _load_device_full(KIT)
    bound, materialized, skipped, full_resources, _name = _discover_full(
        resources,
        oic_res,
        seeds,
        ("oic.wk.d", "oic.d.airconditioner"),
    )
    return bound, materialized, skipped, full_resources


def test_kit_materializes_five_indoor_subdevices():
    """The kit enumerates five indoor subdevices and rejects none -- the master
    reports numofsubdevice 5 and every /<uuid>/device/0 answers."""
    _bound, materialized, skipped, _full = _kit()
    assert len(materialized) == 5
    assert skipped == []
    assert all(s.kind == "prefixed" for s in materialized)


def test_kit_master_surfaces_no_entities_of_its_own():
    """The Wi-Fi kit only fronts the indoor units: its /temperatures/indoor is
    the Hide placeholder and it has no mode/energy, so it must add no unprefixed
    entity. Every state key belongs to a subdevice."""
    bound, _materialized, _skipped, full = _kit()
    state = flatten(bound, full)
    assert state, "expected the subdevices to produce entities"
    assert all(k.startswith("subdevice_") for k in state), sorted(
        k for k in state if not k.startswith("subdevice_")
    )


def test_kit_leaves_no_unbound_hrefs():
    """Every resource on the master and on each indoor subdevice binds or is
    covered, so the coverage-gap repair stays clear across the whole kit."""
    _bound, materialized, _skipped, full = _kit()
    # The master Wi-Fi kit, judged on its own canonical resources.
    master = parse_device0_batch(json.loads(_KIT_FIXTURE.read_text())["device0"])
    master_reg = resolve(master, device_types=("oic.wk.d", "oic.d.airconditioner"))
    assert master_reg is not None
    master_unbound = []
    discover(
        master,
        master_reg.capabilities,
        master_reg.pattern_capabilities,
        log=master_unbound.append,
    )
    assert master_unbound == [], master_unbound
    # Each indoor subdevice, judged on its own canonical view.
    for sub in materialized:
        view = canonical_view(sub, full, materialized)
        sub_reg = resolve(view, device_types=()) or master_reg
        unbound = []
        discover(view, sub_reg.capabilities, sub_reg.pattern_capabilities, log=unbound.append)
        assert unbound == [], f"{sub.key}: {unbound}"


def test_kit_outdoor_temperature_is_diagnostic_and_disabled():
    """All five indoor units report the same outdoor reading (#789), so the
    sensor is diagnostic and disabled by default."""
    bound, _materialized, _skipped, full = _kit()
    outside = [
        b
        for b in bound
        if b.desc.key == "outdoor_temperature" and b.capability.href == "/temperatures/outside/vs/0"
    ]
    assert len(outside) == 5, "one outdoor_temperature per indoor subdevice"
    for b in outside:
        assert b.desc.entity_category == "diagnostic", b.href
        assert b.desc.enabled_default is False, b.href

    # All five read 13.9 from /temperatures/outside; the /mode OutdoorTemp copy
    # is pruned by its exists_fn on this board.
    state = flatten(bound, full)
    outdoor = {k: v for k, v in state.items() if k.endswith("_outdoor_temperature")}
    assert len(outdoor) == 5, sorted(outdoor)
    assert set(outdoor.values()) == {13.9}, outdoor


def test_kit_exposes_no_windlong():
    """windlong is untested on hardware, so its href stays ignored and binds no entity."""
    bound, _materialized, _skipped, full = _kit()
    assert "/modeoption/windlong/vs/0" in _AC_IGNORED
    # The capture has no /modeoption resource to test a write against.
    assert not any("modeoption" in href for href in full), [h for h in full if "modeoption" in h]
    assert not any("windlong" in (b.desc.key or "").lower() for b in bound)
    state = flatten(bound, full)
    assert not any("windlong" in k.lower() for k in state)
