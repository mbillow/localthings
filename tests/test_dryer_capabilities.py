"""Tests for dryer support and washer/dryer consistency (issue #14)."""
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import for_device_by_model
from custom_components.localthings.registry.capabilities import dryer, ignored, laundry
from custom_components.localthings.registry.discovery import discover

from tests.conftest import _load_device


def _dryer():
    resources = _load_device('dryer')
    info = resources['/information/vs/0']
    reg = for_device_by_model(
        info['x.com.samsung.da.modelNum'], info['x.com.samsung.da.description'],
    )
    return reg, resources


def _state():
    reg, resources = _dryer()
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
    return flatten(bound, resources)


def test_dryer_model_resolves_to_dryer_registry():
    reg, _ = _dryer()
    assert reg is not None and reg.name == 'dryer'


def test_no_unbound_hrefs():
    """Every resource in the issue #14 dump binds or is ignored -- clears the
    coverage-gap repair."""
    reg, resources = _dryer()
    unbound = []
    discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert unbound == []


def test_expected_entities_present():
    state = _state()
    for key in ('buzzer_sound', 'cycle', 'power_switch', 'child_lock',
                'remote_control', 'dry_level', 'wrinkle_prevent', 'energy_kwh'):
        assert key in state, key


def test_job_beginning_status_reads_current_status():
    """The dump carries x.com.samsung.da.currentStatus (not the old
    jobBeginingStatus field the dryer descriptor used to read), so the sensor
    is populated instead of blank."""
    assert _state()['job_beginning_status'] == 'None'  # the dump's currentStatus value


def test_power_watts_gated_for_dead_sentinel():
    """instantaneousPower is the dead '-500' sentinel on this dryer, so the
    misleading 0 W power sensor is suppressed; cumulative energy still shows."""
    state = _state()
    assert 'power_watts' not in state
    assert 'energy_kwh' in state


def test_course_bound_to_shared_course_vs_0():
    """Dryer course uses the shared /course/vs/0 cycle select, with the
    translation key built from the device's own course table (see
    laundry.cycle_select) -- confirmed dryers report Table_03, matching
    the shipped dryer_cycle_table_03 translations, consistent with
    washer/dishwasher."""
    assert dryer.DRYER_COURSE.href == '/course/vs/0'
    desc = next(e for e in dryer.DRYER_COURSE.entities if e.key == 'cycle')
    assert callable(desc.translation_key)
    table_03 = {'/st/dryercourse/vs/0': {'x.com.samsung.da.st.courseTable': 'Table_03'}}
    assert desc.translation_key(table_03) == 'dryer_cycle_table_03'
    assert desc.translation_key({}) is None
    assert desc.options is laundry.cycle_options
    rep = {'x.com.samsung.da.options': ['Course_16', 'GMT_02']}
    assert desc.rep_fn(rep) == '16'


def test_st_dryercourse_is_ignored():
    """/st/dryercourse/vs/0 re-encodes the course exposed via /course/vs/0 and
    is globally ignored -- the mirror of /st/washercourse/vs/0."""
    ignored_hrefs = {c.href for c in ignored.IGNORED}
    assert '/st/dryercourse/vs/0' in ignored_hrefs
    assert '/st/washercourse/vs/0' in ignored_hrefs
