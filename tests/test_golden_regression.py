import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / 'fixtures' / 'golden'


def _new_state_keys(name, resources):
    from custom_components.localthings.registry.by_type import (
        for_device, for_device_by_model, for_device_by_resources,
    )
    from custom_components.localthings.registry.discovery import discover
    from custom_components.localthings.registry.adapter import flatten
    otn = resources.get('/otninformation/vs/0', {})
    one_ui = otn.get('swVersionInfo', {}).get('oneUiVersion', '')
    info = resources.get('/information/vs/0', {})
    reg = for_device(one_ui) if one_ui else None
    if reg is None:
        reg = for_device_by_model(
            info.get('x.com.samsung.da.modelNum', ''),
            info.get('x.com.samsung.da.description', ''),
        )
    if reg is None:
        reg = for_device_by_resources(resources)
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


def test_registry_reproduces_golden_state_keys_for_water_purifier_coffee():
    """TP2X_WATERPURIFIER_20K coffee-capable variant (issue #107) adds
    /favorite/coffee/vs/0, /favorite/hotwater/vs/0, and three static
    coffee-recipe resources not present in issue #90's original dump."""
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


def test_registry_reproduces_golden_state_keys_for_air_purifier_avt():
    """AVT-WW-TP1-23-AXX500 -- reports no oneUiVersion and carries no '_TVTL_'
    token; resolved via the 'AVT-' modelNum prefix fallback in
    for_device_by_model. Shares the air_purifier registry with the TVTL family
    but binds fan speed on /wind/strength and filter on /filter/hepafilter."""
    from tests.conftest import _load_device
    resources = _load_device('air_purifier_avt')
    golden = json.loads((GOLDEN / 'air_purifier_avt.json').read_text())
    state_keys = _new_state_keys('air_purifier_avt', resources)
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
