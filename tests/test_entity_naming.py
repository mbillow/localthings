"""Tests for LocalThingsEntity's display-name derivation
(custom_components/localthings/entity.py) -- the explicit-name,
device-given-instance-name, and href-derived fallbacks.
"""
from custom_components.localthings.entity import LocalThingsEntity
from custom_components.localthings.registry.capability import Capability
from custom_components.localthings.registry.discovery import BoundEntity
from custom_components.localthings.registry.entities import BinarySensorDesc


class _FakeCoordinator:
    device_serial = 'TEST-SERIAL'

    def __init__(self, last_resources=None):
        self.last_resources = last_resources or {}


def _make_entity(desc, href='/x/vs/0', key_override=None, instance='', instance_name=None):
    capability = Capability(href=href, entities=(desc,))
    bound = BoundEntity(href=href, capability=capability, desc=desc,
                         instance=instance, key_override=key_override,
                         instance_name=instance_name)
    return LocalThingsEntity(_FakeCoordinator(), bound)


def test_explicit_name_wins_over_everything():
    desc = BinarySensorDesc(key='enabled', name='Explicit Name')
    entity = _make_entity(desc, instance_name='Cubed Ice')
    assert entity._attr_name == 'Explicit Name'


def test_instance_name_prefixes_the_derived_suffix():
    """Issue #27: an ice maker's device-given name ("Cubed Ice") replaces
    the href-derived instance label ("Icemaker One") as the name prefix,
    keeping the same entity-specific suffix."""
    desc = BinarySensorDesc(key='enabled')
    entity = _make_entity(desc, key_override='icemaker_one_enabled',
                           instance_name='Cubed Ice')
    assert entity._attr_name == 'Cubed Ice Enabled'


def test_no_instance_name_falls_back_to_derived_state_key():
    desc = BinarySensorDesc(key='enabled')
    entity = _make_entity(desc, key_override='icemaker_one_enabled')
    assert entity._attr_name == 'Icemaker One Enabled'
