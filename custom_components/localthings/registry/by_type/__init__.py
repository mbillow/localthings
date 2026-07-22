"""Per-device-type registries."""
from typing import Optional

from ._base import DeviceRegistry
from . import airconditioner, dishwasher, dryer, oven, refrigerator, washer

__all__ = ['DeviceRegistry', '_type_key', 'for_device', 'for_device_by_model']


_REGISTRY_BY_KEY: dict[str, DeviceRegistry] = {
    'airconditioner': airconditioner.REGISTRY,
    'air_conditioner': airconditioner.REGISTRY,
    'dishwasher': dishwasher.REGISTRY,
    'dryer': dryer.REGISTRY,
    'oven': oven.REGISTRY,
    'refrigerator': refrigerator.REGISTRY,
    'washer': washer.REGISTRY,
}


def _type_key(one_ui_version: str) -> str:
    """Convert oneUiVersion string to registry key.

    Args:
        one_ui_version: String like '7.0 Dishwasher' or 'Oven'.

    Returns:
        Lowercase key with version prefix stripped and spaces/hyphens converted to underscores.

    Examples:
        '7.0 Dishwasher' -> 'dishwasher'
        '7.0 French Door Refrigerator' -> 'french_door_refrigerator'
        'Oven' -> 'oven'
    """
    if ' ' in one_ui_version:
        # Strip version prefix: everything before and including the first space
        suffix = one_ui_version.split(' ', 1)[-1]
    else:
        suffix = one_ui_version

    return suffix.lower().replace(' ', '_').replace('-', '_')


def for_device(one_ui_version: str) -> Optional[DeviceRegistry]:
    """Return the DeviceRegistry for the given oneUiVersion string, or None if unknown.

    Args:
        one_ui_version: Device's oneUiVersion string (e.g., '7.0 Dishwasher').

    Returns:
        DeviceRegistry if a matching registry exists, None otherwise.
    """
    key = _type_key(one_ui_version)
    if key in _REGISTRY_BY_KEY:
        return _REGISTRY_BY_KEY[key]
    # Suffix fallback: e.g. "french_door_refrigerator" ends with "_refrigerator"
    for rkey, reg in _REGISTRY_BY_KEY.items():
        if key.endswith(f'_{rkey}'):
            return reg
    return None


# Consumer-model prefix (first two letters of the '_'-delimited token in
# `description` right before any '/board-info' suffix) -> registry key.
# NOT derived from `modelNum` -- washer and dryer share the same 'DA_WM_'
# internal board-family prefix there, and dishwasher's modelNum contains
# the substring 'WW', so a modelNum-only rule misroutes both.
_CONSUMER_PREFIX_TO_KEY: dict[str, str] = {
    'WW': 'washer',
    'WD': 'washer',
    'WF': 'washer',
    'DV': 'dryer',
    'DW': 'dishwasher',
}


def for_device_by_model(model_num: str, description: str) -> Optional[DeviceRegistry]:
    """Fallback device-type detection for hardware that never reports
    oneUiVersion (confirmed for washers -- their /otninformation/vs/0 has
    no swVersionInfo key at all).

    Args:
        model_num: x.com.samsung.da.modelNum from /information/vs/0.
        description: x.com.samsung.da.description from /information/vs/0.

    Returns:
        DeviceRegistry if the consumer-model code or modelNum resolves to a
        known type, None otherwise.
    """
    token = (description or '').split('/', 1)[0].rsplit('_', 1)[-1]
    key = _CONSUMER_PREFIX_TO_KEY.get(token[:2].upper())
    if key is None and '_REF_' in (model_num or ''):
        key = 'refrigerator'
    # Room air conditioners (e.g. ARTIK051_PRAC_20K) report no oneUiVersion and
    # a modelNum carrying the '_PRAC_' (Package Room Air Conditioner) token.
    if key is None and '_PRAC_' in (model_num or ''):
        key = 'airconditioner'
    return _REGISTRY_BY_KEY.get(key) if key else None
