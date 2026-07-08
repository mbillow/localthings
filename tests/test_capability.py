from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.entities import BinarySensorDesc


def test_capability_defaults():
    c = Capability(href='/kidslock/vs/0',
                   entities=(BinarySensorDesc(key='child_lock', field='x.com.samsung.da.kidsLock'),))
    assert c.poll_tier == 'cold'
    assert c.active_when is None
    assert len(c.entities) == 1


def test_capability_is_frozen():
    c = Capability(href='/kidslock/vs/0', entities=())
    try:
        c.href = '/other/vs/0'
    except Exception:
        return
    raise AssertionError("expected frozen dataclass")
