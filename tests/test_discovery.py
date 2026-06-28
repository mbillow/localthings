from samsung_appliance.registry.capability import Capability
from samsung_appliance.registry.discovery import (
    BoundEntity, discover, instance_suffix,
)
from samsung_appliance.registry.entities import BinarySensorDesc

LOCK = Capability(
    href='/kidslock/vs/0',
    entities=(BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock'),),
)
REG = {LOCK.href: LOCK}


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
    reg = {'/door/vs/0': cap, '/door/vs/1': cap}
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
    bound = discover(resources, {'/kidslock/vs/0': cap}, log=seen.append)
    assert len(bound) == 1
    assert any('mystery' in m for m in seen)
