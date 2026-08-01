import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / 'fixtures' / 'golden'


def _new_state_keys(name, resources):
    from custom_components.localthings.registry.by_type import resolve
    from custom_components.localthings.registry.discovery import discover
    from custom_components.localthings.registry.adapter import flatten
    reg = resolve(resources)
    if reg is None:
        from custom_components.localthings.registry.registry import CAPABILITIES
        caps, pats = CAPABILITIES, []
    else:
        caps, pats = reg.capabilities, reg.pattern_capabilities
    bound = discover(resources, caps, pats)
    state = flatten(bound, resources)
    return sorted(state.keys())


@pytest.mark.parametrize('name,ip', [
    ('dishwasher', '10.0.0.129'),
    ('refrigerator', '10.0.0.254'),
])
def test_registry_reproduces_golden_state_keys(name, ip, request):
    from tests.conftest import _load_resources
    resources = _load_resources(ip)
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys = _new_state_keys(name, resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer():
    from tests.conftest import _load_device
    resources = _load_device('washer')
    golden = json.loads((GOLDEN / 'washer.json').read_text())
    state_keys = _new_state_keys('washer', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_ehs():
    from tests.conftest import _load_device
    resources = _load_device('ehs')
    golden = json.loads((GOLDEN / 'ehs.json').read_text())
    state_keys = _new_state_keys('ehs', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer_wa8000t():
    """Top-load washer (WA8000T, issue #106) reports no oneUiVersion and
    used the 'WA' consumer-model prefix, previously unmapped in
    _CONSUMER_PREFIX_TO_KEY -- fell back to 'unknown'."""
    from tests.conftest import _load_device
    resources = _load_device('washer_wa8000t')
    golden = json.loads((GOLDEN / 'washer_wa8000t.json').read_text())
    state_keys = _new_state_keys('washer_wa8000t', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_dryer():
    from tests.conftest import _load_device
    resources = _load_device('dryer')
    golden = json.loads((GOLDEN / 'dryer.json').read_text())
    state_keys = _new_state_keys('dryer', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_dryer_dve50a8600():
    """DVE50A8600V/A3 (issue #79) -- description pairs two model numbers
    ('..._DVE50A8800_8600/...'), so the true 'DV' consumer-model token sits
    one segment before the actual last segment ('8600'). The old
    last-segment-only check missed it and fell back to 'unknown'; resolved
    via _consumer_model_key scanning segments from the end."""
    from tests.conftest import _load_device
    resources = _load_device('dryer_dve50a8600')
    golden = json.loads((GOLDEN / 'dryer_dve50a8600.json').read_text())
    state_keys = _new_state_keys('dryer_dve50a8600', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner():
    from tests.conftest import _load_device
    resources = _load_device('airconditioner')
    golden = json.loads((GOLDEN / 'airconditioner.json').read_text())
    state_keys = _new_state_keys('airconditioner', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_dehumidifier():
    """TP1X_DA_AC_DHM_01001_0000 (issue #88, AY18CG7500GED) shares the DA_AC_
    board family with the room-AC models but carries the '_DHM_' token;
    resolved via the '_DHM_' modelNum fallback in for_device_by_model into a
    dedicated dehumidifier registry (target humidity, operating mode, reused
    AC filter/auto-clean/mute-once capabilities)."""
    from tests.conftest import _load_device
    resources = _load_device('dehumidifier')
    golden = json.loads((GOLDEN / 'dehumidifier.json').read_text())
    state_keys = _new_state_keys('dehumidifier', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_water_purifier():
    """TP2X_WATERPURIFIER_20K (issue #90) reports no oneUiVersion; resolved
    via the 'WATERPURIFIER' modelNum/description fallback into a dedicated
    water_purifier registry (dispense settings, sterilize/filter status,
    favorite capacity, and the three lock switches)."""
    from tests.conftest import _load_device
    resources = _load_device('water_purifier')
    golden = json.loads((GOLDEN / 'water_purifier.json').read_text())
    state_keys = _new_state_keys('water_purifier', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_water_purifier_ailite_25k():
    """AILITE_WATERPURIFIER_25K (issue #196, RWP70F15ANW) -- a coffee-capable
    water purifier on an AILITE_DA-REF-WATERPURIFIER board, whose modelNum's
    'REF' token would otherwise misroute it to the refrigerator registry (see
    TestBoardTokenAmbiguity's carve-out). Also the first dump to expose
    /cup/state/vs/0, /statistic/pour/vs/0, and the settings/sound/* trio on
    this device type, and a hot_water_temperature select that must gate off
    (no supportedHotTemperatures reported) rather than surface as 'unknown'."""
    from tests.conftest import _load_device
    resources = _load_device('water_purifier_ailite_25k')
    golden = json.loads((GOLDEN / 'water_purifier_ailite_25k.json').read_text())
    state_keys = _new_state_keys('water_purifier_ailite_25k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_water_purifier_coffee():
    """TP2X_WATERPURIFIER_20K coffee-capable variant (issue #107) adds
    /favorite/coffee/vs/0, /favorite/hotwater/vs/0, and three static
    coffee-recipe resources not present in issue #90's original dump.

    This fixture's /setting/waterpurifier/vs/0 turns out to report no
    supportedHotTemperatures either (only hotwaterLevel/hotwaterRange, the
    same shape issue #196 surfaced) -- so hot_water_temperature dropped out
    of this golden when the #196 fix (see water_purifier.DISPENSE's
    exists_fn) landed. This fixture was quietly hitting the same 'unknown'
    bug all along; the registry-level golden just had no way to show it
    since flatten()'s state dict doesn't model select option membership."""
    from tests.conftest import _load_device
    resources = _load_device('water_purifier_coffee')
    golden = json.loads((GOLDEN / 'water_purifier_coffee.json').read_text())
    state_keys = _new_state_keys('water_purifier_coffee', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_cooktop():
    from tests.conftest import _load_device
    resources = _load_device('cooktop')
    golden = json.loads((GOLDEN / 'cooktop.json').read_text())
    state_keys = _new_state_keys('cooktop', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp1x_ref_21k_us():
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_tp1x_ref_21k_us')
    golden = json.loads(
        (GOLDEN / 'refrigerator_tp1x_ref_21k_us.json').read_text()
    )
    state_keys = _new_state_keys('refrigerator_tp1x_ref_21k_us', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp1x_ref_21k_eu():
    """TP1X_REF_21K, EU region variant (issue #165) -- self-reports
    oneUiVersion "7.0 Refrigerator" like the US variant, but additionally
    carries /rm/control/vs/0 (a bare resource-monitoring poll-interval
    config, added to the global ignore list) that the US dump doesn't
    report at all. Door sensors (the reporter's actual ask) were already
    covered by fridge.DOOR_GENERIC/DOORS_FALLBACK -- this href was the only
    gap keeping the coverage repair open."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_tp1x_ref_21k_eu')
    golden = json.loads(
        (GOLDEN / 'refrigerator_tp1x_ref_21k_eu.json').read_text()
    )
    state_keys = _new_state_keys('refrigerator_tp1x_ref_21k_eu', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_range_hood():
    from tests.conftest import _load_device
    resources = _load_device('range_hood')
    golden = json.loads((GOLDEN / 'range_hood.json').read_text())
    state_keys = _new_state_keys('range_hood', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer_flexwash():
    """FlexWash twin washers (WV-prefix consumer model, e.g. WV55M9600AW)
    report no oneUiVersion and previously fell through for_device_by_model's
    consumer-prefix map entirely -- issue #19."""
    from tests.conftest import _load_device
    resources = _load_device('washer_flexwash')
    golden = json.loads((GOLDEN / 'washer_flexwash.json').read_text())
    state_keys = _new_state_keys('washer_flexwash', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer_dryer_combo():
    """Washer/dryer combo units carry a writable dryLevel field on
    /washer/vs/0 itself, with no separate dryer resource -- issue #22."""
    from tests.conftest import _load_device
    resources = _load_device('washer_dryer_combo')
    golden = json.loads((GOLDEN / 'washer_dryer_combo.json').read_text())
    state_keys = _new_state_keys('washer_dryer_combo', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_artik051_ref_17k():
    """ARTIK051_REF_17K's Cool Select Zone pantry compartment
    (/status/pantry/one/vs/0) -- issue #20."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_artik051_ref_17k')
    golden = json.loads((GOLDEN / 'refrigerator_artik051_ref_17k.json').read_text())
    state_keys = _new_state_keys('refrigerator_artik051_ref_17k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_artik051_dongle_ref_cooler():
    """RR40M7165WW (issue #78) -- the same ARTIK051_DONGLE_REF household
    dongle family as issue #77's freezer, but the fridge half: reports
    /door/cooler/0 *and* /door/onedoorfreezer/vs/0 (the latter apparently
    shared firmware naming, not an actual second freezer compartment) plus
    /temperature/{current,desired}/cooler/0. Same pipe-delimited modelNum
    detection gap and DOOR_GENERIC field-name gap as #77, same fix."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_artik051_dongle_ref_cooler')
    golden = json.loads((GOLDEN / 'refrigerator_artik051_dongle_ref_cooler.json').read_text())
    state_keys = _new_state_keys('refrigerator_artik051_dongle_ref_cooler', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_artik051_dongle_ref():
    """ARTIK051_DONGLE_REF standalone freezer (issues #77/#83) -- reports no
    oneUiVersion and a pipe-delimited modelNum ('..._DONGLE_REF|<rest>')
    that the old '_REF_' substring check missed entirely; resolved via the
    segment-based check in for_device_by_model. Its door
    (/door/onedoorfreezer/vs/0) and temperature
    (/temperature/{current,desired}/freezer/0) hrefs only bind through
    fridge.py's pattern capabilities, which the 'unknown' fallback never
    tries -- so this also regression-tests that those resources bind at
    all once routed to the right registry."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_artik051_dongle_ref')
    golden = json.loads((GOLDEN / 'refrigerator_artik051_dongle_ref.json').read_text())
    state_keys = _new_state_keys('refrigerator_artik051_dongle_ref', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp2x_ref_20k():
    """TP2X_REF_20K -- CV_FDR_-prefixed flex zone (issue #32) plus the extra
    energy fields (cumulativeConsumption/monthlyConsumption/
    thismonthlyConsumption) surfaced by issue #26."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_tp2x_ref_20k')
    golden = json.loads((GOLDEN / 'refrigerator_tp2x_ref_20k.json').read_text())
    state_keys = _new_state_keys('refrigerator_tp2x_ref_20k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp2x_ref_20k_kimchi():
    """A different physical unit reporting the same "TP2X_REF_20K" modelNum
    string as the fixture above (issue #26's second reporter) -- a
    3-compartment kimchi refrigerator with no flex zone, doors/icemaker, or
    freezer/cooler split, but its own /status/kimchi/<slot>/vs/0 and
    /kimchidoors/top/vs/0 resources (fridge.KIMCHI_ZONE/KIMCHI_DOOR_GENERIC)."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_tp2x_ref_20k_kimchi')
    golden = json.loads((GOLDEN / 'refrigerator_tp2x_ref_20k_kimchi.json').read_text())
    state_keys = _new_state_keys('refrigerator_tp2x_ref_20k_kimchi', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_ac_tp1x_da_ac_rac_01011():
    """Newer AC firmware (Tizen Lite, oneUiVersion "7.0 Air conditioner"; model
    TP1X_DA-AC-RAC-01011) reports temperature via the vendor /temperatures/vs/0
    items[] resource and adds a /light/vs/0 display light, with extra vendor
    housekeeping hrefs -- issue #17 for this model class (PR #36)."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_tp1x_da_ac_rac_01011')
    golden = json.loads(
        (GOLDEN / 'airconditioner_tp1x_da_ac_rac_01011.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_tp1x_da_ac_rac_01011', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp2x_rac_20k():
    """TP2X_RAC_20K (issue #37) -- reports no oneUiVersion; resolved via the
    '_RAC_' modelNum token fallback in for_device_by_model."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_tp2x_rac_20k')
    golden = json.loads((GOLDEN / 'airconditioner_tp2x_rac_20k.json').read_text())
    state_keys = _new_state_keys('airconditioner_tp2x_rac_20k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_caww_tp2():
    """A-CAWW-TP2-20-COMMON (issue #52, System AC / multi-indoor-unit
    commercial install) reports no oneUiVersion and no '_RAC_'/'_PRAC_'
    token; resolved via the '-CAWW-' modelNum fallback in
    for_device_by_model. Otherwise binds cleanly against the existing
    airconditioner registry -- same TP1X/TP2X-class resource surface, plus
    one SAC-only installation-topology resource (ignored)."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_caww_tp2')
    golden = json.loads((GOLDEN / 'airconditioner_caww_tp2.json').read_text())
    state_keys = _new_state_keys('airconditioner_caww_tp2', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_window_ac():
    """TP1X_DA_AC_WAC_01001_0000 (issue #87, Bespoke Window AC AW06C7155EWAZ)
    reports no oneUiVersion and carries the '_WAC_' (Window Air Conditioner)
    token instead of '_RAC_'/'_PRAC_'; resolved via the '_WAC_' modelNum
    fallback in for_device_by_model. Otherwise binds cleanly against the
    existing airconditioner registry with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_window_ac')
    golden = json.loads((GOLDEN / 'airconditioner_window_ac.json').read_text())
    state_keys = _new_state_keys('airconditioner_window_ac', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp1x_rac():
    """TP1X_DA-AC-RAC-01001_0000 (issue #38) -- fuller RAC board with display
    light, self-check, mute-once, and a current-limit setting."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_tp1x_rac')
    golden = json.loads((GOLDEN / 'airconditioner_tp1x_rac.json').read_text())
    state_keys = _new_state_keys('airconditioner_tp1x_rac', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_tp1x_rac_coolonly():
    """TP1X_DA-AC-RAC-01001 cool-only global variant (issue #91) whose
    /otninformation/vs/0 ships no swVersionInfo block -- resolves via the
    'RAC' board token in its modelNum."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_tp1x_rac_coolonly')
    golden = json.loads((GOLDEN / 'airconditioner_tp1x_rac_coolonly.json').read_text())
    state_keys = _new_state_keys('airconditioner_tp1x_rac_coolonly', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_windfree():
    """ARTIK051_PRAC_20K, WindFree-capable unit (issue #75) -- same modelNum
    family as the original issue #17 fixture, but its /mode/convenient/vs/0
    additionally reports Nano/NanoSleep/MotionDirect/MotionIndirect, its
    /wind/direction/vs/0 reports Left_And_Right, and /humidity/vs/0's
    fivepercentHumidity is actually populated."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_windfree')
    golden = json.loads((GOLDEN / 'airconditioner_windfree.json').read_text())
    state_keys = _new_state_keys('airconditioner_windfree', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_lnx_rac_heatpump():
    """Lennox-branded heat pump on the Samsung RAC board family (issue #173,
    modelNum TP1X_LNX-AC-RAC-01001_0000) -- routes via the existing '-RAC-'
    token, same registry as the plain RAC family. Adds two AI-feature
    resources not seen on prior AC dumps: /mds/absencepowersaving/vs/0
    (absence-detection power saving) and /option/motiondetectwind/stateful/vs/0
    (avoid-direct-wind-on-motion), both exposed read-only per the 'don't
    guess' rule."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_lnx_rac_heatpump')
    golden = json.loads(
        (GOLDEN / 'airconditioner_lnx_rac_heatpump.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_lnx_rac_heatpump', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_range():
    """Range/cooktop-oven combo (model TP1X_DA-KS-RANGE-0102X, issue #44) --
    reports no oneUiVersion; resolved via the '-RANGE-' modelNum token
    fallback in for_device_by_model. Reuses the oven family's cavity/
    setpoint/mode/operational-state capabilities and adds range.py's
    per-burner capabilities for the 4 burners this dump reports."""
    from tests.conftest import _load_device
    resources = _load_device('range')
    golden = json.loads((GOLDEN / 'range.json').read_text())
    state_keys = _new_state_keys('range', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_induction_cooktop():
    """Standalone induction cooktop, no oven attached (model
    TP1X_DA-KS-COOKTOP-01011, issue #86) -- reports no oneUiVersion and a
    hyphenated '-COOKTOP-' modelNum token, resolved via
    for_device_by_model into its own 'induction_cooktop' registry (not
    cooktop.REGISTRY, which is the unrelated NA9300K gas-cooktop family)."""
    from tests.conftest import _load_device
    resources = _load_device('induction_cooktop')
    golden = json.loads((GOLDEN / 'induction_cooktop.json').read_text())
    state_keys = _new_state_keys('induction_cooktop', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_range_no_info():
    """NE63B8411SS (issue #74) -- reports no oneUiVersion *and* no
    /information/vs/0 at all, so neither for_device nor
    for_device_by_model has anything to key off; resolved via the 'Bake'-
    in-supportedModes + /cooktopmonitoring/vs/0 signature in
    for_device_by_resources. This board's local API has no per-burner
    /cooktop/status/vs/0 array either -- only the coarse
    /cooktopmonitoring/vs/0 monitoring resource covered by range.py's
    COOKTOP_MONITORING."""
    from tests.conftest import _load_device
    resources = _load_device('range_no_info')
    golden = json.loads((GOLDEN / 'range_no_info.json').read_text())
    state_keys = _new_state_keys('range_no_info', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_purifier():
    """ARTIK051_TVTL_18K (issue #56) -- reports no oneUiVersion; resolved via
    the '_TVTL_' modelNum token fallback in for_device_by_model."""
    from tests.conftest import _load_device
    resources = _load_device('air_purifier')
    golden = json.loads((GOLDEN / 'air_purifier.json').read_text())
    state_keys = _new_state_keys('air_purifier', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_oven():
    """Wall oven (model TP1X_DA-KS-OVEN-0107X, issue #55) -- reports no
    oneUiVersion; resolved via the '-OVEN-' modelNum token fallback in
    for_device_by_model, mirroring the '-RANGE-' fallback added for
    issue #44. Before that fallback existed the device type came back
    'unknown' and every href fell through to the global CAPABILITIES
    registry instead of the oven family's own."""
    from tests.conftest import _load_device
    resources = _load_device('oven')
    golden = json.loads((GOLDEN / 'oven.json').read_text())
    state_keys = _new_state_keys('oven', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_washer_wa55a7700av():
    """DA_WM_TP1_21_COMMON top-load washer (model WA55A7700AV, issue #111)
    -- a different board generation than the WA8000T's TP2_20_COMMON,
    reached through the same 'WA' consumer-model-prefix fallback (issue
    #106). Binds cleanly against the existing washer registry with zero
    unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('washer_wa55a7700av')
    golden = json.loads((GOLDEN / 'washer_wa55a7700av.json').read_text())
    state_keys = _new_state_keys('washer_wa55a7700av', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_range_ne8300d():
    """TP1X_DA-KS-RANGE-0102X (model NE8300D, issue #112) -- reports no
    oneUiVersion; resolved via the '-RANGE-' modelNum token fallback.
    Binds cleanly against the existing range registry, including
    /cooktopmonitoring/vs/0 via range.COOKTOP_MONITORING, with zero
    unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('range_ne8300d')
    golden = json.loads((GOLDEN / 'range_ne8300d.json').read_text())
    state_keys = _new_state_keys('range_ne8300d', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_range_ne63a6511():
    """NE63A6511SS/AA (issue #138) -- reports no /information/vs/0 at all,
    same shape as issue #74's NE63B8411SS; resolved via the same
    'Bake'-in-supportedModes + /cooktopmonitoring/vs/0 signature in
    for_device_by_resources. Binds cleanly against the existing range
    registry with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('range_ne63a6511')
    golden = json.loads((GOLDEN / 'range_ne63a6511.json').read_text())
    state_keys = _new_state_keys('range_ne63a6511', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_ara_ww_tp1_22():
    """ARA-WW-TP1-22-COMMON wall-mount RACs (model AR10/13/18BYEAAWKNME,
    issues #115/#116/#117/#120) report no oneUiVersion and no
    '_RAC_'/'-RAC-' token -- resolved via the 'ARA-WW-' modelNum fallback.
    Same resource surface as the other TP1X-class room ACs; binds cleanly
    against the existing airconditioner registry with zero unbound hrefs,
    so no new device type was needed."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_ara_ww_tp1_22')
    golden = json.loads((GOLDEN / 'airconditioner_ara_ww_tp1_22.json').read_text())
    state_keys = _new_state_keys('airconditioner_ara_ww_tp1_22', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_windfree_oscillation():
    """TP1X_DA-AC-RAC-01011_0000 Bespoke AI WindFree Deluxe (model
    AR60H10D1JWNME, issue #126) -- the same board family as the
    airconditioner_tp1x_da_ac_rac_01011 fixture, but a newer firmware
    variant reporting no /wind/direction/vs/0 at all: swing lives on the
    2-axis /wind/oscillation/vs/0 resource instead (airconditioner.
    HREF_WIND_OSCILLATION, climate.py's oscillation fallback), and it adds
    an /anomalyload/vs/0 overload-response resource (airconditioner.
    ANOMALY_LOAD, read-only per the 'don't guess' rule). Binds cleanly
    with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_windfree_oscillation')
    golden = json.loads(
        (GOLDEN / 'airconditioner_windfree_oscillation.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_windfree_oscillation', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_microwave_mw7300b():
    """TP1X_DA-KS-MICROWAVE-01041 combi microwave (model MW7300B, issue
    #121) -- reports no oneUiVersion; resolved via the '-MICROWAVE-'
    modelNum token fallback onto its own microwave registry. Shares the
    oven board family's operational-state/door/connected/recipe-cook
    Capability objects (reused directly from oven.py), but has its own
    cooking-mode vocabulary, setpoint bounds, and cavity power-level sensor
    (capabilities/microwave.py) -- this device's initial routing folded it
    into the oven registry (issue #121); split into its own device type per
    user feedback that microwaves shouldn't show up as ovens."""
    from tests.conftest import _load_device
    resources = _load_device('microwave_mw7300b')
    golden = json.loads((GOLDEN / 'microwave_mw7300b.json').read_text())
    state_keys = _new_state_keys('microwave_mw7300b', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_microwave_me7500d():
    """TP1X_DA-KS-MICROWAVE-01051 plain microwave (model ME7500D, issues
    #137/#142) -- the same microwave registry as MW7300B above, but this
    board also reports the built-in vent fan's `/hood/fanspeed/vs/0`
    resource, previously unbound. Bound via range_hood.HOOD_FAN (reused
    directly, same resource shape a standalone range hood reports); unlike
    a standalone hood this board has no sibling `/power/0` or
    `/power/vs/0` resource, so fan.py's LocalThingsRangeHoodFan treats
    fan speed 0 as the off state instead of writing a separate power
    resource -- see its `_speed_zero_is_off` check. This board also has
    no `/temperatures/vs/0` or `x.com.samsung.da.hood.autoOperation`
    field, unlike MW7300B, so `setpoint`/`current_temp_c` and
    `automatic_operation` are correctly absent here."""
    from tests.conftest import _load_device
    resources = _load_device('microwave_me7500d')
    golden = json.loads((GOLDEN / 'microwave_me7500d.json').read_text())
    state_keys = _new_state_keys('microwave_me7500d', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_microwave_me7500d_lamp_high():
    """Same TP1X_DA-KS-MICROWAVE-01051/ME7500D board as microwave_me7500d
    above, but this live capture (issue #152) is the first to report a
    non-Off Lamp token ('Lamp_High'). Locks in that the lamp switch reads
    it as on rather than the previously-hardcoded 'On'-only comparison."""
    from tests.conftest import _load_device
    resources = _load_device('microwave_me7500d_lamp_high')
    golden = json.loads((GOLDEN / 'microwave_me7500d_lamp_high.json').read_text())
    state_keys = _new_state_keys('microwave_me7500d_lamp_high', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_purifier_tp1x_da_ac_air():
    """TP1X_DA-AC-AIR-01031_0000 (issue #130) self-reports oneUiVersion
    '7.0 Air purifier' (unused for routing) and resolves via its 'AIR'
    board token onto the existing
    air_purifier registry (shared with the older ARTIK051_TVTL family via
    per-href match_fn discrimination -- see capabilities/air_purifier.py).
    Its /mode/vs/0 reports modes/supportedModes directly (Smart/Max/Mid/
    WindFree/Sleep) rather than the older family's packed options[] scheme,
    which the new air_purifier.FAN capability now binds to a real `fan`
    entity (PRESET_MODE, not an ordered speed -- see fan.py). Binds with
    zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('air_purifier_tp1x_da_ac_air')
    golden = json.loads((GOLDEN / 'air_purifier_tp1x_da_ac_air.json').read_text())
    state_keys = _new_state_keys('air_purifier_tp1x_da_ac_air', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_vacuum_station():
    """A-VSKR-TP1-22-VS9500AL stick-vacuum clean/auto-empty station (issue
    #131) -- reports no oneUiVersion; resolved via the new '-VSKR-'
    modelNum fallback onto a new vacuum_station registry (see
    capabilities/vacuum_station.py's module docstring for why this needed
    a new device type rather than reusing an existing one: the dump has no
    vacuum-body state at all, only station-specific dustbag/dustbin/
    UV-sanitize resources that share no hrefs with anything else already
    modeled). Binds with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('vacuum_station')
    golden = json.loads((GOLDEN / 'vacuum_station.json').read_text())
    state_keys = _new_state_keys('vacuum_station', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_artik051_krac_18k():
    """ARTIK051_KRAC_18K (issue #136) -- reports no oneUiVersion, and its
    '_KRAC_' token was invisible to for_device_by_model's '_RAC_' check (the
    'K' sits between the underscore and 'RAC'), so it fell back to 'unknown'
    and exposed nothing but power. Same ARTIK051 board generation as the
    '_TVTL_' air purifier: no /wind/* resources at all (fan and vane live in
    /airflow/vs/0), no /mode/convenient/vs/0 (the preset is a Comode_* token),
    and several settings carried as /mode/vs/0 options[] tokens."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_artik051_krac_18k')
    golden = json.loads(
        (GOLDEN / 'airconditioner_artik051_krac_18k.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_artik051_krac_18k', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_tp1x_rac_01001():
    """TP1X_DA-AC-RAC-01001_0000 (model AR07C9150HZN, issue #155) -- binds
    cleanly against the existing airconditioner registry with zero unbound
    hrefs (the registry/discovery side was never the gap here). Its
    /wind/strength/vs/0 reports supportedModes "0"/"31"-"35" instead of the
    "0"-"4" scale climate.py's _DEVICE_TO_FAN was built from, which silently
    dropped every fan speed but Auto -- see
    test_airconditioner_tp1x_rac_01001_fan.py for the climate-level fix."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_tp1x_rac_01001')
    golden = json.loads(
        (GOLDEN / 'airconditioner_tp1x_rac_01001.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_tp1x_rac_01001', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_tp1x_rac_odor_controller():
    """TP1X_DA-AC-RAC-01001_0000 -- reporter's dump: fan (0-4 wind-strength
    scale) and WindFree (Nano/NanoSleep convenient-mode codes) already bind
    via the composite climate entity, no code change needed there. The
    genuine gap was /mode/vs/0's SmartCoolClean_/ProgressSmartClean_ option
    tokens (the cloud custom.airConditionerOdorController capability),
    previously unbound to any entity -- now odor_controller_active/
    odor_controller_progress."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_tp1x_rac_odor_controller')
    golden = json.loads(
        (GOLDEN / 'airconditioner_tp1x_rac_odor_controller.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_tp1x_rac_odor_controller', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_dresser():
    """DA_DF_A51_20_COMMON AirDresser (model DF8600T, issue #162) -- reports
    no oneUiVersion; resolved via the '_DF_' modelNum token fallback. Has no
    /wm/editcourse/vs/0 at all, so the course select's option list comes
    entirely from laundry.cycle_options' supportedOptions fallback. Binds
    cleanly against the new air_dresser registry with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('air_dresser')
    golden = json.loads((GOLDEN / 'air_dresser.json').read_text())
    state_keys = _new_state_keys('air_dresser', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_dresser_tp2_20():
    """DA_DF_TP2_20_COMMON AirDresser (model DF9500A, issue #157) -- a
    different board generation than issue #162's DA_DF_A51_20_COMMON, also
    routed via the '_DF_' modelNum fallback into the same air_dresser
    registry. Unlike #162's board, this one populates /wm/editcourse/vs/0's
    editCourseList directly (no supportedOptions fallback needed) and
    reports two AirDresser-specific resources #162 doesn't have:
    /st/airdressercourse/vs/0 (course table id, ignored.py) and
    /airdresseroption/sanitize/vs/0 (air_dresser.AIR_DRESSER_SANITIZE).
    Binds cleanly with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('air_dresser_tp2_20')
    golden = json.loads((GOLDEN / 'air_dresser_tp2_20.json').read_text())
    state_keys = _new_state_keys('air_dresser_tp2_20', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_dresser_tp1_21():
    """DA_DF_TP1_21_COMMON AirDresser (model DF3000B, issue #208) -- another
    board generation routed via the '_DF_' modelNum token fallback into the
    same air_dresser registry as #162's and #157's boards (its dump also
    happens to be the first AirDresser one to carry /oic/d's device type,
    'x.com.st.d.steamcloset' -- see test_by_type.py's
    TestForDeviceByOicType for that mapping's own coverage). The first
    AirDresser dump to report /buzzersound/vs/0 (laundry.BUZZER_SOUND,
    added for this issue -- #162's and #157's boards don't report it at
    all). Binds cleanly with zero unbound hrefs; course codes for this
    board's table still aren't identified (same as #162's), so 'cycle'
    remains the raw code rather than a table-specific translation."""
    from tests.conftest import _load_device
    resources = _load_device('air_dresser_tp1_21')
    golden = json.loads((GOLDEN / 'air_dresser_tp1_21.json').read_text())
    state_keys = _new_state_keys('air_dresser_tp1_21', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_monitor():
    """Samsung Air Monitor Plus (ASM-KR-TP1-22-ACMB1M, issue #210) -- a
    standalone battery-powered air-quality sensor puck, the first device
    this integration has no controllable-appliance concept for at all (no
    /power/*, only /energy/battery/vs/0). Routes via both /oic/d
    ('x.com.st.d.airqualitysensor') and the 'ASM' modelNum board token.
    Reuses air_purifier.AIR_QUALITY's /sensors/vs/0 decode
    (common.sensor_item_value) for dust/fine_dust/super_fine_dust/odor/
    clean_level, adding a CO2 reading those families don't report. Binds
    cleanly with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('air_monitor')
    golden = json.loads((GOLDEN / 'air_monitor.json').read_text())
    state_keys = _new_state_keys('air_monitor', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_air_purifier_vtww():
    """A-VTWW-TP2-21-COMMON BESPOKE Cube Air (issue #151) -- reports no
    oneUiVersion; resolved via the '-VTWW-' modelNum token fallback into
    the existing air_purifier registry. Its fan lives on
    /wind/strength/vs/0 (air_purifier.WIND_STRENGTH_FAN) rather than the
    /mode/vs/0 FAN the other two board generations in this registry use.
    Binds cleanly with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('air_purifier_vtww')
    golden = json.loads((GOLDEN / 'air_purifier_vtww.json').read_text())
    state_keys = _new_state_keys('air_purifier_vtww', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_fac_bora():
    """TP2X_FAC_BORA_21K Wind-Free 2-in-1 (floor-standing + wall-mounted
    indoor units sharing one outdoor unit and one local IP, issues
    #150/#153) -- reports no oneUiVersion; resolved via the '_FAC_'
    modelNum fallback. Binds cleanly against the existing airconditioner
    registry (including a real climate entity, the actual reported gap)
    with zero unbound hrefs -- /subdevices/vs/0 and /runn/vs/0 are the only
    hrefs this board reports that no other AC family does, both added to
    _AC_IGNORED as undocumented/not-locally-actionable."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_fac_bora')
    golden = json.loads((GOLDEN / 'airconditioner_fac_bora.json').read_text())
    state_keys = _new_state_keys('airconditioner_fac_bora', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def _new_subdevice_aware_state_keys(name):
    """Like _new_state_keys, but runs the full subdevice-aware pipeline
    (enumerate_subdevices + discover_partitioned, issue #177) instead of a
    single discover() call, so the golden for a composite-device fixture
    captures every materialized subdevice's keys
    (subdevice1_-/subdevice_<uuid>_-prefixed), not just the master's."""
    from custom_components.localthings.registry.adapter import flatten
    from tests.conftest import _discover_full, _load_device_full
    resources, oic_res, seeds = _load_device_full(name)
    bound, _materialized, _skipped, full_resources, _device_type_name = _discover_full(
        resources, oic_res, seeds,
    )
    state = flatten(bound, full_resources)
    return sorted(state.keys())


def test_registry_reproduces_golden_state_keys_for_airconditioner_artik051_dongle_fac_18k():
    """The reporter's ARTIK051_DONGLE_FAC_18K (issue #177, Pattern A -- indexed
    siblings): a real v0.16.0 dump with a genuine second indoor subdevice at
    `/device/1` (subdevice1_-prefixed keys below) and an unused SmartThings
    slot at `/device/2` that answers its seed but never produces a
    materialized subdevice (see DESIGN-177.md section 4 and
    test_subdevice_discovery.py's explicit "/device/2 produces no entities"
    assertion) -- so this golden has no `subdevice2_`-prefixed keys at all,
    which is the point."""
    name = 'airconditioner_artik051_dongle_fac_18k'
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys = _new_subdevice_aware_state_keys(name)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_cac():
    """TP1X_DA-AC-CAC-01001_0000 (issue #191) -- fell back to 'unknown' in
    0.16.0 when oneUiVersion detection was dropped, since 'CAC' had never
    been added to the modelNum board-token table. Resolved via the new 'CAC'
    token onto the existing airconditioner registry. Not fully covered yet --
    ten hrefs remain unbound (edge lighting, PM1 filter, stateful light,
    absence-clean, four sound-settings resources, smart-sensing-cooling, UV
    LED), all genuinely new to this board generation and out of scope for
    the routing fix; see test_airconditioner_cac.py for the documented gap."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_cac')
    golden = json.loads((GOLDEN / 'airconditioner_cac.json').read_text())
    state_keys = _new_state_keys('airconditioner_cac', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_fac_bora_2in1():
    """The reporter's TP2X_FAC_BORA_21K (issue #177, Pattern B -- UUID-prefixed
    tree): device0/oic_res are real; the wall-mounted subdevice's own
    /information/vs/0 is real (confirmed live by the reporter), the rest of
    its seed tree is constructed (see the fixture's own seeds_note) -- just
    enough to bind a real climate card under the
    `subdevice_6c2dff6dee5cdad16a5e000000000001_` prefix below. Distinct from
    tests/fixtures/airconditioner_fac_bora_device.json, which is
    deliberately left unchanged as the redacted-subdeviceIdList regression
    case (zero subdevices)."""
    name = 'airconditioner_fac_bora_2in1'
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys = _new_subdevice_aware_state_keys(name)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_fac_bora_205_flat():
    """The same reporter's same physical TP2X_FAC_BORA_21K unit as the _2in1
    fixture above, but a later capture (issue #205) where /<uuid>/device/0 doesn't
    answer -- contrary to what that fixture's own seed batch assumed the
    Collection endpoint would do. device0/oic_res are real; the only
    UUID-prefixed data is the one href ever actually confirmed live
    (/information/vs/0, same real capture the _2in1 fixture uses), fed
    through registry.subdevices.enumerate_subdevices' per-href flat
    fallback instead of a Collection batch. /information/vs/0 alone binds
    no entity, so the candidate is found but never materializes -- this
    golden has no `subdevice_...`-prefixed keys at all, same shape as
    tests/fixtures/golden/airconditioner_fac_bora.json, which is the point:
    a device whose sibling can't yet be confirmed live must regress to
    exactly the master-only state, never a phantom or partial subdevice."""
    name = 'airconditioner_fac_bora_205_flat'
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys = _new_subdevice_aware_state_keys(name)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_artik051_krac_18k_slot():
    """The issue #214 reporter's single-split AR12NXWXCWKNEU: a
    non-composite ARTIK051_KRAC_18K whose `/device/1` answers a full-shaped
    batch with every operational rep empty {} and a populated
    /energy/consumption/vs/1. The lifetime kWh counter used to be enough to
    pass discover_partitioned's liveness gate, materializing a phantom
    second air conditioner in HA; a meter is now excluded from that gate
    (subdevices._has_live_primary_entity), so this golden must stay
    byte-identical to airconditioner_artik051_krac_18k.json -- master keys
    only, no `subdevice1_` prefix anywhere -- which is what
    test_krac_18k_energy_only_slot_is_not_materialized asserts structurally."""
    name = 'airconditioner_artik051_krac_18k_slot'
    golden = json.loads((GOLDEN / f'{name}.json').read_text())
    state_keys = _new_subdevice_aware_state_keys(name)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )
    master_golden = json.loads(
        (GOLDEN / 'airconditioner_artik051_krac_18k.json').read_text()
    )
    assert set(state_keys) == set(master_golden['state_keys'])


def test_registry_reproduces_golden_state_keys_for_air_purifier_avt_ww():
    """AVT-WW-TP1-23-AXX500 (issue #190) -- next-gen BESPOKE Cube Air board;
    reports device_type 'unknown' with empty oneUiVersion because 'VTWW' as a
    whole token doesn't match this board's 'AVT'/'WW' split. Resolved via the
    new 'AVT' modelNum board-token fallback into the existing air_purifier
    registry. Binds cleanly with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('air_purifier_avt_ww')
    golden = json.loads((GOLDEN / 'air_purifier_avt_ww.json').read_text())
    state_keys = _new_state_keys('air_purifier_avt_ww', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_airconditioner_artik051_krac_energy():
    """AR12NXWXCWKNEU/ARTIK051_KRAC_18K (issue #193) -- same legacy board
    generation as the artik051_krac_18k fixture, but this dump has a nonzero
    cumulativePower. Locks in state_keys; the actual /100000 scale fix is
    asserted separately in test_airconditioner_capabilities.py since golden
    only compares key sets, not values."""
    from tests.conftest import _load_device
    resources = _load_device('airconditioner_artik051_krac_energy')
    golden = json.loads(
        (GOLDEN / 'airconditioner_artik051_krac_energy.json').read_text()
    )
    state_keys = _new_state_keys('airconditioner_artik051_krac_energy', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_refrigerator_definite_cooler():
    """RT42DG6630B1FZ (issue #186) -- a single-door "cooler only" fridge whose
    /temperature/definite/cooler/vs/0 doesn't match either
    TEMP_CURRENT_GENERIC's '/temperature/current/' or TEMP_SETPOINT's
    '/temperature/desired/' href prefix, so temperature control was entirely
    unbound. Resolved via the new DEFINITE_TEMPERATURE_COOLER capability
    (a select over the device's own discrete supportedList, not a
    continuous NumberDesc range -- 5 and 6 aren't valid setpoints on this
    model). Binds cleanly with zero unbound hrefs."""
    from tests.conftest import _load_device
    resources = _load_device('refrigerator_definite_cooler')
    golden = json.loads(
        (GOLDEN / 'refrigerator_definite_cooler.json').read_text()
    )
    state_keys = _new_state_keys('refrigerator_definite_cooler', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_registry_reproduces_golden_state_keys_for_range_ne6516a():
    """NE6516A-class range (issue #183) -- no /information/vs/0, resolved via
    the same 'Bake'-in-supportedModes + /cooktopmonitoring/vs/0 signature as
    the other no-info range fixtures. Its /mode/vs/0 options[] carries
    EnergySaving_On and BurnerOnAlert_Off (previously unbound entirely) but
    no fastpreheat_*/NaturalSteam_* tokens at all -- locks in that those two
    switches now correctly stay unbound instead of binding as always-off,
    does-nothing phantom controls."""
    from tests.conftest import _load_device
    resources = _load_device('range_ne6516a')
    golden = json.loads((GOLDEN / 'range_ne6516a.json').read_text())
    state_keys = _new_state_keys('range_ne6516a', resources)
    assert set(state_keys) == set(golden['state_keys']), (
        f"state_keys mismatch:\n"
        f"  extra:   {sorted(set(state_keys) - set(golden['state_keys']))}\n"
        f"  missing: {sorted(set(golden['state_keys']) - set(state_keys))}"
    )


def test_resources_from_batch_preferred_over_flat():
    from tests.conftest import _resources_from_dump
    dump = {
        'device0': [
            {'di': 'device'},  # [0] device-level rep, skipped
            {'href': '/foo', 'rep': {'x': 1}},
        ],
        'resources': {'/foo': {'x': 99}},
    }
    result = _resources_from_dump(dump)
    assert result == {'/foo': {'x': 1}}
