import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _resources_from_dump(dump: dict) -> dict[str, dict]:
    from samsung_appliance.bridge import _parse_device0_batch
    return _parse_device0_batch(dump['device0'])


def _load_device(name: str) -> dict[str, dict]:
    data = json.loads((FIXTURES / f'{name}_device.json').read_text())
    return _resources_from_dump(data)


def _load_resources(ip: str) -> dict[str, dict]:
    """Legacy IP-based loader — maps known IPs to named fixtures."""
    _ip_to_name = {
        '10.0.0.129': 'dishwasher',
        '10.0.0.254': 'refrigerator',
    }
    name = _ip_to_name.get(ip)
    if name is None:
        raise ValueError(f"No fixture for IP {ip!r} — add a scrubbed fixture to tests/fixtures/")
    return _load_device(name)


@pytest.fixture
def dishwasher_resources() -> dict[str, dict]:
    return _load_device('dishwasher')


@pytest.fixture
def fridge_resources() -> dict[str, dict]:
    return _load_device('refrigerator')
