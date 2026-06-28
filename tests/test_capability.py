from samsung_appliance.registry.capability import Capability
from samsung_appliance.registry.entities import BinarySensorDesc


def test_capability_defaults():
    c = Capability(rt='x.com.samsung.da.kidsLock',
                   entities=(BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock'),))
    assert c.poll_tier == 'warm'
    assert c.observe is True
    assert c.active_when is None
    assert len(c.entities) == 1


def test_capability_is_frozen():
    c = Capability(rt='x', entities=())
    try:
        c.rt = 'y'
    except Exception:
        return
    raise AssertionError("expected frozen dataclass")
