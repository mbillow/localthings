from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import (
    BoundEntity, discover, instance_suffix,
)
from custom_components.localthings.registry.entities import BinarySensorDesc

LOCK = Capability(
    href='/kidslock/vs/0',
    entities=(BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock'),),
)
REG = {LOCK.href: [LOCK]}


def test_instance_suffix():
    assert instance_suffix('/kidslock/vs/0') == ''
    assert instance_suffix('/door/vs/1') == '_1'
    assert instance_suffix('/icemaker/vs/2') == '_2'


def test_discover_binds_present_capability():
    resources = {'/kidslock/vs/0': {'x.com.samsung.da.kidsLock': 'On'}}
    bound = discover(resources, REG)
    assert len(bound) == 1
    assert bound[0].href == '/kidslock/vs/0'
    assert bound[0].desc.key == 'child_lock'
    assert bound[0].instance == ''


def test_discover_skips_unknown_href():
    seen = []
    resources = {'/mystery/vs/0': {'x.com.samsung.da.mystery': 'x'}}
    bound = discover(resources, REG, log=seen.append)
    assert bound == []
    assert any('mystery' in m for m in seen)


def test_discover_multi_instance_suffixes():
    cap = Capability(href='/door/vs/0',
                     entities=(BinarySensorDesc(key='door', field='x.com.samsung.da.doorState'),))
    resources = {
        '/door/vs/0': {'x.com.samsung.da.doorState': 'Open'},
        '/door/vs/1': {'x.com.samsung.da.doorState': 'Closed'},
    }
    reg = {'/door/vs/0': [cap], '/door/vs/1': [cap]}
    bound = discover(resources, reg)
    insts = sorted(b.instance for b in bound)
    assert insts == ['', '_1']


def test_discover_logs_unregistered_href():
    seen = []
    cap = Capability(href='/kidslock/vs/0',
                     entities=(BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock'),))
    resources = {
        '/kidslock/vs/0': {'x.com.samsung.da.kidsLock': 'On'},
        '/mystery/vs/0': {'x.com.samsung.da.mystery': 'x'},
    }
    bound = discover(resources, {'/kidslock/vs/0': [cap]}, log=seen.append)
    assert len(bound) == 1
    assert any('mystery' in m for m in seen)


# ---------------------------------------------------------------------------
# New tests for Task 2: pattern caps, rt_filter, match_fn
# ---------------------------------------------------------------------------

def test_discover_pattern_cap_binds_unmatched_href():
    """Pattern cap with rt_filter binds unmatched hrefs; exact-href cap does not
    steal unmatched hrefs."""
    cooler_cap = Capability(
        href='/door/cooler/0',
        entities=(BinarySensorDesc(key='cooler_door', field='x.com.samsung.da.doorState'),),
    )
    door_pattern = Capability(
        href=None,
        rt_filter='oic.r.door',
        entities=(BinarySensorDesc(key='door', field='x.com.samsung.da.doorState'),),
    )
    resources = {
        '/door/cooler/0': {'x.com.samsung.da.doorState': 'Closed', 'rt': ['oic.r.door']},
        '/door/wine/0':   {'x.com.samsung.da.doorState': 'Open',   'rt': ['oic.r.door']},
    }
    reg = {'/door/cooler/0': [cooler_cap]}
    bound = discover(resources, reg, pattern_caps=[door_pattern])

    hrefs = [b.href for b in bound]
    # exact cap claims /door/cooler/0
    assert '/door/cooler/0' in hrefs
    # pattern cap claims /door/wine/0 (unmatched by registry)
    assert '/door/wine/0' in hrefs
    # /door/cooler/0 is not also claimed by the pattern cap
    cooler_bindings = [b for b in bound if b.href == '/door/cooler/0']
    assert all(b.capability is cooler_cap for b in cooler_bindings)


def test_discover_pattern_cap_skips_already_bound_href():
    """Pattern cap must not bind an href already claimed by an exact-href cap."""
    exact_cap = Capability(
        href='/door/cooler/0',
        entities=(BinarySensorDesc(key='cooler_door', field='x.com.samsung.da.doorState'),),
    )
    pattern = Capability(
        href=None,
        rt_filter='oic.r.door',
        entities=(BinarySensorDesc(key='generic_door', field='x.com.samsung.da.doorState'),),
    )
    resources = {'/door/cooler/0': {'x.com.samsung.da.doorState': 'Closed', 'rt': ['oic.r.door']}}
    reg = {'/door/cooler/0': [exact_cap]}
    bound = discover(resources, reg, pattern_caps=[pattern])

    # exactly one binding for /door/cooler/0 — the exact cap, not the pattern
    assert len(bound) == 1
    assert bound[0].capability is exact_cap


def test_discover_match_fn_filters_wrong_device():
    """A cap with match_fn must not bind when its condition is not met."""
    oven_cap = Capability(
        href='/oven/vs/0',
        match_fn=lambda r, rs: '/oven/vs/0' in rs,
        entities=(BinarySensorDesc(key='oven_status', field='x.com.samsung.da.state'),),
    )
    # resources does NOT include /oven/vs/0 — match_fn should fail
    resources = {'/oven/vs/0': {'x.com.samsung.da.state': 'Ready'}}
    reg = {'/oven/vs/0': [oven_cap]}

    # Without /oven/vs/0 present as a key in resources, match_fn should be False
    resources_no_oven = {'/other/vs/0': {'x.com.samsung.da.state': 'Ready'}}
    reg_no_oven = {}
    # Put oven cap on a different href to test match_fn rejection
    oven_cap2 = Capability(
        href='/other/vs/0',
        match_fn=lambda r, rs: '/oven/vs/0' in rs,
        entities=(BinarySensorDesc(key='oven_status', field='x.com.samsung.da.state'),),
    )
    bound = discover(resources_no_oven, {'/other/vs/0': [oven_cap2]})
    # match_fn returns False → no bindings
    assert bound == []


def test_discover_rt_filter_gates_binding():
    """Cap with rt_filter must not bind a rep whose rt list does not match."""
    oven_mode_cap = Capability(
        href='/mode/vs/0',
        rt_filter='x.com.samsung.da.ovenMode',
        entities=(BinarySensorDesc(key='oven_mode', field='x.com.samsung.da.mode'),),
    )
    # rep has a different rt → should not bind
    resources = {'/mode/vs/0': {'rt': ['x.com.samsung.da.mode'], 'x.com.samsung.da.mode': 'Bake'}}
    bound = discover(resources, {'/mode/vs/0': [oven_mode_cap]})
    assert bound == []
