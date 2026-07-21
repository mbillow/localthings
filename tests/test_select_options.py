"""Tests for LocalThingsSelect's option-list resolution
(custom_components/localthings/select.py) -- the static tuple, options_field,
and callable forms of SelectDesc.options.
"""
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.select import LocalThingsSelect


class _FakeCoordinator:
    device_serial = 'TEST-SERIAL'

    def __init__(self, last_resources):
        self.last_resources = last_resources


def _make_select(desc, href, last_resources):
    capability = Capability(href=href, entities=(desc,))
    bound = BoundEntity(href=href, capability=capability, desc=desc)
    return LocalThingsSelect(_FakeCoordinator(last_resources), bound)


def test_static_options_unaffected():
    desc = SelectDesc(key='x', options=('A', 'B'))
    entity = _make_select(desc, '/x/vs/0', {})
    assert entity.options == ['A', 'B']


def test_options_field_unaffected():
    desc = SelectDesc(key='x', options_field='supported')
    entity = _make_select(desc, '/x/vs/0', {'/x/vs/0': {'supported': ['Lo', 'Hi']}})
    assert entity.options == ['Lo', 'Hi']


def test_callable_options_receives_full_resource_snapshot():
    """A callable options is handed the coordinator's full href->rep
    snapshot, not just this entity's own href's rep -- needed for course
    lists decoded from a sibling resource (see laundry.cycle_options)."""
    calls = []

    def _options_fn(resources):
        calls.append(resources)
        return list(resources.get('/other/vs/0', {}).get('codes', []))

    desc = SelectDesc(key='cycle', translation_key='fake_cycle', options=_options_fn)
    resources = {
        '/x/vs/0': {},
        '/other/vs/0': {'codes': ['1C', '1D']},
    }
    entity = _make_select(desc, '/x/vs/0', resources)
    assert entity.options == ['1c', '1d']
    assert calls == [resources]


def test_callable_options_empty_result():
    desc = SelectDesc(key='cycle', options=lambda resources: [])
    entity = _make_select(desc, '/x/vs/0', {})
    assert entity.options == []
