"""Tests for registry.redact — the safety net for diagnostics downloads."""
import json
from pathlib import Path

from custom_components.localthings.registry.batch import parse_device0_batch
from custom_components.localthings.registry.redact import REDACTED, redact_resources

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def _load(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text())
    return parse_device0_batch(data['device0'])


def test_redacts_known_sensitive_fields_in_dishwasher_dump():
    resources = _load('dishwasher_device.json')
    redacted = redact_resources(resources)

    info = redacted['/information/vs/0']
    assert info['x.com.samsung.da.serialNum'] == REDACTED
    assert info['x.com.samsung.da.otnDUID'] == REDACTED

    wireless = redacted['/wirelessinfo/vs/0']
    assert wireless['macaddressWiFi'] == REDACTED
    assert wireless['macaddressBLE'] == REDACTED

    provisioning = redacted['/voice/provisioning/vs/0']
    headers = provisioning['voice.provisioning.headers']
    assert headers['login_id'] == REDACTED
    deviceinfo = provisioning['voice.provisioning.deviceinfo']
    assert deviceinfo['voice.provisioning.deviceinfo.accesstoken'] == REDACTED
    assert deviceinfo['voice.provisioning.deviceinfo.deviceid'] == REDACTED
    assert deviceinfo['voice.provisioning.deviceinfo.userid'] == REDACTED


def test_ordinary_state_fields_survive_untouched():
    resources = _load('dishwasher_device.json')
    redacted = redact_resources(resources)

    op_state = redacted['/operational/state/vs/0']
    assert op_state['x.com.samsung.da.state'] == 'Run'
    assert op_state['x.com.samsung.da.progress'] == 'Finish'

    power = redacted['/power/vs/0']
    assert power['x.com.samsung.da.power'] == 'On'

    dishwasher = redacted['/dishwasher/vs/0']
    assert dishwasher['x.com.samsung.da.sanitize'] == 'On'
    assert dishwasher['x.com.samsung.da.rinseLevel'] == '4'

    alarms = redacted['/alarms/vs/0']['x.com.samsung.da.items']
    assert alarms[0]['x.com.samsung.da.code'] == 'SNSF_Reached'


def test_redacts_known_sensitive_fields_in_refrigerator_dump():
    resources = _load('refrigerator_device.json')
    redacted = redact_resources(resources)

    info = redacted['/information/vs/0']
    assert info['x.com.samsung.da.serialNum'] == REDACTED

    wireless = redacted['/wirelessinfo/vs/0']
    assert wireless['macaddressWiFi'] == REDACTED
    assert wireless['macaddressBLE'] == REDACTED


def test_redact_resources_does_not_mutate_input():
    resources = _load('dishwasher_device.json')
    original_serial = resources['/information/vs/0']['x.com.samsung.da.serialNum']

    redact_resources(resources)

    assert resources['/information/vs/0']['x.com.samsung.da.serialNum'] == original_serial
