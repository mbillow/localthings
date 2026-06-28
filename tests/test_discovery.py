from samsung_appliance.registry.capability import Capability
from samsung_appliance.registry.discovery import (
    BoundEntity, discover, instance_suffix,
)
from samsung_appliance.registry.entities import BinarySensorDesc

LOCK = Capability(
    rt='x.com.samsung.da.kidsLock',
    entities=(BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock'),),
)
REG = {LOCK.rt: LOCK}


def test_instance_suffix():
    assert instance_suffix('/kidslock/vs/0') == ''
    assert instance_suffix('/door/vs/1') == '_1'
    assert instance_suffix('/icemaker/vs/2') == '_2'


def test_discover_binds_present_capability():
    resources = {'/kidslock/vs/0': {'rt': ['x.com.samsung.da.kidsLock'],
                                     'x.com.samsung.da.kidsLock': 'On'}}
    bound = discover(resources, REG)
    assert len(bound) == 1
    assert bound[0].href == '/kidslock/vs/0'
    assert bound[0].desc.key == 'child_lock'
    assert bound[0].instance == ''


def test_discover_skips_unknown_rt():
    seen = []
    resources = {'/mystery/vs/0': {'rt': ['x.com.samsung.da.mystery']}}
    bound = discover(resources, REG, log=seen.append)
    assert bound == []
    assert any('mystery' in m for m in seen)


def test_discover_multi_instance_suffixes():
    cap = Capability(rt='x.com.samsung.da.door',
                     entities=(BinarySensorDesc(key='door', field='x.com.samsung.da.doorState'),))
    resources = {
        '/door/vs/0': {'rt': ['x.com.samsung.da.door']},
        '/door/vs/1': {'rt': ['x.com.samsung.da.door']},
    }
    bound = discover(resources, {cap.rt: cap})
    insts = sorted(b.instance for b in bound)
    assert insts == ['', '_1']
