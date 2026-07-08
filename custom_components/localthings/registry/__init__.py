"""Device capability registry for Samsung appliances.

Declares each appliance family's resources as Capability objects keyed on
their stable OCF resource href, binds a connected device's live resources
to the matching capabilities, and flattens the result into HA-ready entity
state. The DTLS/CoAP transport itself lives in the smartthings-local
package, not here.
"""
from .adapter import flatten, is_active, _key
from .discovery import BoundEntity, discover
from .registry import CAPABILITIES

__all__ = ['CAPABILITIES', 'discover', 'BoundEntity', 'flatten', 'is_active', '_key']
