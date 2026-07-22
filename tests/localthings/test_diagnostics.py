"""Tests for the diagnostics platform."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from homeassistant.core import HomeAssistant

from custom_components.localthings import diagnostics as diagnostics_mod
from custom_components.localthings.const import DOMAIN
from custom_components.localthings.diagnostics import async_get_config_entry_diagnostics
from custom_components.localthings.registry.redact import REDACTED

_MANIFEST_VERSION = json.loads(
    (Path(__file__).parents[2] / 'custom_components' / 'localthings' / 'manifest.json').read_text()
)['version']


async def test_diagnostics_shape_and_redaction(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diagnostics["device_type"] == 'refrigerator'
    assert diagnostics["one_ui_version"] == '7.0 Refrigerator'
    assert diagnostics["unbound_hrefs"] == []
    assert diagnostics["integration_version"] == _MANIFEST_VERSION
    assert diagnostics["smartthings_local_version"]

    resources = diagnostics["resources"]
    assert resources['/information/vs/0']['x.com.samsung.da.serialNum'] == REDACTED
    assert resources['/wirelessinfo/vs/0']['macaddressWiFi'] == REDACTED
    # Ordinary state survives.
    assert resources['/status/lock/vs/0']['x.com.samsung.da.ado.devicecontrol'] == 'On'


async def test_diagnostics_include_observe_mode_fields(
    hass: HomeAssistant, mock_entry, mock_coordinator_session
) -> None:
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diag['observe_mode'] == 'poll'
    assert diag['observe_subscribed_hrefs'] == []
    assert diag['observe_fallback_hrefs'] == []
    assert 'observe_last_mode_change' in diag
    assert diag['observe_href_freshness_s'] == {}


async def test_dependency_version_read_off_the_event_loop(
    hass: HomeAssistant, mock_entry, mock_coordinator_session, monkeypatch
) -> None:
    """importlib.metadata.version() does blocking disk I/O, so it must run in
    the executor, not on the event loop (issue #9's logs flagged it)."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    loop_thread_id = threading.get_ident()  # this coroutine runs on the loop
    seen: dict[str, int] = {}
    real_pkg_version = diagnostics_mod.pkg_version

    def _spy(name: str) -> str:
        seen['thread_id'] = threading.get_ident()
        return real_pkg_version(name)

    monkeypatch.setattr(diagnostics_mod, 'pkg_version', _spy)

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    assert diag['smartthings_local_version']
    assert seen['thread_id'] != loop_thread_id
