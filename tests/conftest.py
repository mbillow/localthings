import json
from pathlib import Path

import pytest

DUMPS = Path(__file__).resolve().parent.parent / 'local-tools' / 'dumps'


def _load_resources(ip: str) -> dict[str, dict]:
    data = json.loads((DUMPS / f'{ip}.json').read_text())
    # Filter to only dict values (skip non-dict representations like "<timeout>")
    return {k: v for k, v in data['resources'].items() if isinstance(v, dict)}


@pytest.fixture
def dishwasher_resources() -> dict[str, dict]:
    return _load_resources('10.0.0.129')


@pytest.fixture
def fridge_resources() -> dict[str, dict]:
    return _load_resources('10.0.0.254')
