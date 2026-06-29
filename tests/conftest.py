import json
from pathlib import Path

import pytest

DUMPS = Path(__file__).resolve().parent.parent / 'local-tools' / 'dumps'


def _resources_from_dump(dump: dict) -> dict[str, dict]:
    from samsung_appliance.bridge import _parse_device0_batch
    d0 = dump.get('device0')
    if isinstance(d0, list) and len(d0) > 1:
        batch = _parse_device0_batch(d0)
        if batch:
            return batch
    return {k: v for k, v in dump.get('resources', {}).items()
            if isinstance(v, dict)}


def _load_resources(ip: str) -> dict[str, dict]:
    data = json.loads((DUMPS / f'{ip}.json').read_text())
    return _resources_from_dump(data)


@pytest.fixture
def dishwasher_resources() -> dict[str, dict]:
    return _load_resources('10.0.0.129')


@pytest.fixture
def fridge_resources() -> dict[str, dict]:
    return _load_resources('10.0.0.254')
