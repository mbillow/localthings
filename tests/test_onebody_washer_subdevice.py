"""Pattern C subdevice discovery: AWM-WW-AID-26-ONEBODY washer+dryer combo
(issue #241).

The fixture is a live capture from the reporting board (see its
`seeds_note`): the master tree is the dryer (`oic.d.dryer`,
`..._DV80H27H`), and a complete washer tree (`..._WF80H`) answers at
`/<uuid>/device/0`, where the UUID appears *only* as the path prefix of the
`x.com.samsung.da.multidevice` link in `/oic/res` -- no `subdeviceIdList`
(Pattern B's signal), no `/device/<n>` sibling (Pattern A's).
"""
from tests.conftest import FakeCoapSession, _discover_full, _load_device_full

FIXTURE = 'washer_dryer_onebody_awm'
WASHER_UUID = '58b7d338-15c5-97d3-b562-000000000001'


def _discover():
    resources, oic_res, seeds = _load_device_full(FIXTURE)
    return _discover_full(resources, oic_res, seeds)


def test_washer_subdevice_materializes_from_oic_res_uuid_link():
    bound, materialized, skipped, full_resources, device_type_name = _discover()

    assert [s.key for s in materialized] == [WASHER_UUID]
    sub = materialized[0]
    assert sub.kind == 'prefixed'
    assert sub.seed_path == (WASHER_UUID, 'device', '0')
    # Collection mode, not the issue-#205 flat fallback.
    assert sub.flat_hrefs == ()


def test_washer_subdevice_probe_only_fires_for_the_advertised_uuid():
    """Pattern C must not invent candidates: exactly one prefixed seed is
    probed (the UUID from the /oic/res multidevice link), alongside the
    bounded speculative Pattern A probes this board 4.04s."""
    from custom_components.localthings.registry.subdevices import (
        enumerate_subdevices,
    )

    resources, oic_res, seeds = _load_device_full(FIXTURE)
    probes: dict[str, bool] = {}
    sess = FakeCoapSession(seeds)
    candidates, _extra = enumerate_subdevices(
        sess, resources, oic_res, probe_log=probes.__setitem__,
    )

    assert probes[f'/{WASHER_UUID}/device/0'] is True
    assert probes['/device/1'] is False
    assert probes['/device/2'] is False
    # No flat-fallback flood: the seed Collection answered, so no per-href
    # probes under the prefix beyond the seed itself.
    prefixed_probes = [h for h in probes if h.startswith(f'/{WASHER_UUID}/')]
    assert prefixed_probes == [f'/{WASHER_UUID}/device/0']
    assert [c.key for c in candidates] == [WASHER_UUID]


def test_washer_subdevice_binds_its_own_primary_entities():
    """The washer tree carries its own /operational/state and /power -- the
    liveness gate must see live primary entities, and the bound set must
    carry washer-side keys under the subdevice key prefix."""
    bound, materialized, _skipped, _full, _name = _discover()
    sub = materialized[0]

    sub_keys = {b.desc.key for b in bound if b.subdevice.key == sub.key}
    assert sub_keys, 'washer subdevice bound no entities at all'
    # Its own operational state and its own power switch/sensor -- the two
    # things SmartThings cloud splits this unit over.
    assert any('machine_state' in k for k in sub_keys), sub_keys
    assert any('power' in k for k in sub_keys), sub_keys


def test_master_dryer_entities_unchanged_by_washer_discovery():
    """Additive only: the master (dryer) side must keep binding the same
    entity keys with and without the washer candidate materializing."""
    resources, _oic_res, _seeds = _load_device_full(FIXTURE)

    # Without any subdevices (oic_res withheld, seeds empty -> washer never
    # discovered).
    bound_solo, materialized_solo, _s, _f, _n = _discover_full(
        resources, [], {},
    )
    assert materialized_solo == []
    solo_keys = {(b.href, b.desc.key) for b in bound_solo}

    bound, materialized, _s2, _f2, _n2 = _discover()
    sub = materialized[0]
    main_keys = {
        (b.href, b.desc.key) for b in bound if b.subdevice.key != sub.key
    }

    assert main_keys == solo_keys
