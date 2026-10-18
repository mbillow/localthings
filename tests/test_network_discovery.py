"""Following an appliance whose DHCP lease moved (issue #469).

Home Assistant's dhcp integration hands a discovery flow three fields --
address, hostname and MAC -- and only the MAC identifies a unit: three of
#469's air conditioners answer to one hostname. So these cover the two
halves that make the MAC usable: reading the one the appliance reports, and
what the flow does with a sighting of it.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_DHCP, ConfigEntryState, ConfigFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import config_flow as cf
from custom_components.localthings.const import (
    CONF_CA_CERT_PEM,
    CONF_CA_KEY_PEM,
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_MAC,
    CONF_PORT,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.identity import (
    WIRELESS_INFO_HREF,
    DeviceIdentity,
    resolve_mac,
)
from tests.conftest import _load_device

MAC = "d8:5d:4c:11:22:33"
WIFI_REP = {"macaddressWiFi": "D8:5D:4C:11:22:33", "macaddressBLE": "d8:5d:4c:11:22:34"}


def _sighting(ip: str, mac: str = "d85d4c112233") -> DhcpServiceInfo:
    """A sighting in the shape the dhcp integration builds: the MAC always
    lowercase and unseparated, the hostname lowercased (and, here, shared by
    every unit of the model -- which is the point)."""
    return DhcpServiceInfo(ip=ip, hostname="samsung-room-ac", macaddress=mac)


def _entry(hass: HomeAssistant, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Air conditioner ({data.get(CONF_HOST, '10.0.0.5')})",
        unique_id=f"localthings_{data.get(CONF_DEVICE_KEY, 'UNIT-A')}",
        data={
            CONF_HOST: "10.0.0.5",
            CONF_PORT: 49154,
            CONF_CA_CERT_PEM: "CA-CERT",
            CONF_CA_KEY_PEM: "CA-KEY",
            CONF_LEAF_CERT_PEM: "LEAF-CERT",
            CONF_LEAF_KEY_PEM: "LEAF-KEY",
            CONF_DEVICE_KEY: "UNIT-A",
            CONF_SERIAL: "0J5F7CAT100123",
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


# --------------------------------------------------------------------------
# Reading the MAC off the device
# --------------------------------------------------------------------------


def test_the_reported_mac_is_normalized_to_the_registry_form():
    """Whatever separators the board uses, the stored value has to be the
    one format_mac produces, or the connection never matches a sighting."""
    for reported in ("D8:5D:4C:11:22:33", "d85d4c112233", "D8-5D-4C-11-22-33"):
        assert resolve_mac({WIRELESS_INFO_HREF: {"macaddressWiFi": reported}}) == MAC


def test_a_board_with_nothing_usable_to_report_yields_no_mac():
    cases = [
        {},
        {WIRELESS_INFO_HREF: {}},
        {WIRELESS_INFO_HREF: {"macaddressBLE": "d85d4c112234"}},
        {WIRELESS_INFO_HREF: {"macaddressWiFi": ""}},
        {WIRELESS_INFO_HREF: {"macaddressWiFi": "d85d4c11"}},
        {WIRELESS_INFO_HREF: {"macaddressWiFi": "zz:5d:4c:11:22:33"}},
        # An unflashed radio: shared by every unit of the family, so binding
        # to it would point every entry at whichever one answered last.
        {WIRELESS_INFO_HREF: {"macaddressWiFi": "00:00:00:00:00:00"}},
        {WIRELESS_INFO_HREF: {"macaddressWiFi": None}},
    ]
    for resources in cases:
        assert resolve_mac(resources) is None, resources


def test_every_dump_in_the_corpus_resolves_to_a_connection_or_to_nothing(
    all_device_fixtures,
):
    """Half the corpus reports no MAC and the rest reaches this repo with the
    address redacted (registry/redact.py) or replaced by a scrubbed one, so
    what this pins is the shape: a dump either yields an address the device
    registry will accept as a connection, or None. Anything in between would
    be a connection several entries could share."""
    resolved = {name: resolve_mac(res) for name, res in all_device_fixtures.items()}
    assert {mac for mac in resolved.values() if mac is not None} and all(
        mac is None or re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac)
        for mac in resolved.values()
    ), resolved
    # The two fixtures scrubbed with a well-formed address rather than
    # **REDACTED** are what keep that first branch exercised at all.
    assert resolved["dishwasher"] == "aa:bb:cc:00:01:01"
    assert resolved["air_dresser"] is None


# --------------------------------------------------------------------------
# The DHCP step
# --------------------------------------------------------------------------


async def _dhcp(hass: HomeAssistant, info: DhcpServiceInfo):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_DHCP}, data=info
    )


async def test_a_sighting_of_a_known_mac_moves_the_entry(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(hass, **{CONF_MAC: MAC})
    entry.mock_state(hass, ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _dhcp(hass, _sighting("10.0.0.77"))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.77"
    reload.assert_called_once_with(entry.entry_id)


async def test_a_stored_mac_matches_however_it_was_written(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """An entry stored before resolve_mac normalized anything, or by a board
    that shouts its address, still has to match a lowercase sighting."""
    entry = _entry(hass, **{CONF_MAC: "D8-5D-4C-11-22-33"})

    await _dhcp(hass, _sighting("10.0.0.77"))

    assert entry.data[CONF_HOST] == "10.0.0.77"


async def test_a_sighting_at_the_same_address_wakes_a_retrying_entry(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """The address didn't move, so there is nothing to write -- but the
    sighting is proof the appliance is back on the network, which a poll of
    an unreachable device can't produce (issue #295). HA reloads a
    SETUP_RETRY entry on any discovery for exactly that reason."""
    entry = _entry(hass, **{CONF_MAC: MAC})
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _dhcp(hass, _sighting("10.0.0.5"))

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.5"
    reload.assert_called_once_with(entry.entry_id)


@pytest.fixture
def no_listener_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Core 2026.12 stops reloading an entry with an update listener through
    `updates=` or async_update_reload_and_abort (issue #647), and ours has
    one, so a flow that leans on either fails here."""
    abort = ConfigFlow._abort_if_unique_id_configured

    def _abort(self, updates=None, *args, **kwargs):
        assert updates is None, "reload left to _abort_if_unique_id_configured(updates=...)"
        return abort(self, None, *args, **kwargs)

    def _helper(self, *args, **kwargs):
        raise AssertionError("reload left to async_update_reload_and_abort")

    monkeypatch.setattr(ConfigFlow, "_abort_if_unique_id_configured", _abort)
    monkeypatch.setattr(ConfigFlow, "async_update_reload_and_abort", _helper)


async def _listener(*_) -> None:
    pass


async def test_a_move_reloads_an_entry_with_an_update_listener(
    hass: HomeAssistant,
    enable_custom_integrations,
    no_listener_reload,
):
    entry = _entry(hass, **{CONF_MAC: MAC})
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.add_update_listener(_listener)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _dhcp(hass, _sighting("10.0.0.77"))

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "10.0.0.77"
    reload.assert_called_once_with(entry.entry_id)


async def test_a_move_reloads_a_retrying_entry_once(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(hass, **{CONF_MAC: MAC})
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _dhcp(hass, _sighting("10.0.0.77"))

    assert entry.data[CONF_HOST] == "10.0.0.77"
    reload.assert_called_once_with(entry.entry_id)


async def test_a_sighting_at_the_same_address_leaves_a_loaded_entry_alone(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(hass, **{CONF_MAC: MAC})
    entry.mock_state(hass, ConfigEntryState.LOADED)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _dhcp(hass, _sighting("10.0.0.5"))

    reload.assert_not_called()


async def test_a_sighting_brings_the_address_in_the_title_with_it(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(hass, **{CONF_MAC: MAC})

    await _dhcp(hass, _sighting("10.0.0.77"))

    assert entry.title == "Air conditioner (10.0.0.77)"


async def test_a_sighting_leaves_a_title_the_user_made_their_own(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(hass, **{CONF_MAC: MAC})
    hass.config_entries.async_update_entry(entry, title="Bedroom AC")

    await _dhcp(hass, _sighting("10.0.0.77"))

    assert entry.title == "Bedroom AC"
    assert entry.data[CONF_HOST] == "10.0.0.77"


async def test_a_sighting_of_an_unknown_mac_changes_nothing(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """HA matched the MAC against the device registry, so the row exists --
    but an entry that has never stored one has nothing to be sure with, and
    guessing from the address is how a neighbouring appliance's lease gets
    written onto this entry."""
    entry = _entry(hass)

    result = await _dhcp(hass, _sighting("10.0.0.77"))

    assert result["reason"] == "no_entry_for_mac"
    assert entry.data[CONF_HOST] == "10.0.0.5"


async def test_one_appliance_moving_leaves_its_siblings_alone(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """#469's install is three units of one model on one hostname. Only the
    MAC tells them apart, so only the sighted one may move."""
    moved = _entry(hass, **{CONF_MAC: MAC})
    sibling = _entry(
        hass,
        **{
            CONF_MAC: "d8:5d:4c:99:88:77",
            CONF_HOST: "10.0.0.6",
            CONF_DEVICE_KEY: "UNIT-B",
        },
    )

    await _dhcp(hass, _sighting("10.0.0.77"))

    assert moved.data[CONF_HOST] == "10.0.0.77"
    assert sibling.data[CONF_HOST] == "10.0.0.6"


# --------------------------------------------------------------------------
# Reconfigure
# --------------------------------------------------------------------------


def _probe_result(**overrides) -> dict:
    return {
        "port": 49155,
        "device_key": "UNIT-A",
        "ocf_device_id": "UNIT-A",
        "serial": "0J5F7CAT100123",
        "model": "AR12TXFCAWKNEU",
        "manufacturer": "Samsung Electronics",
        "device_type_name": "Air conditioner",
        "device_type_recognized": True,
        "leaf_cert_pem": "LEAF-CERT-2",
        "leaf_key_pem": "LEAF-KEY-2",
        "mac": MAC,
        **overrides,
    }


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, host: str, probe: dict):
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    with (
        patch.object(cf, "_probe_and_validate", return_value=probe),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: host})


async def test_reconfigure_reloads_an_entry_with_an_update_listener(
    hass: HomeAssistant,
    enable_custom_integrations,
    no_listener_reload,
):
    entry = _entry(hass, **{CONF_MAC: MAC})
    entry.add_update_listener(_listener)

    result = await _reconfigure(hass, entry, "10.0.0.77", _probe_result())

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.77"


async def test_reconfigure_moves_the_entry_and_learns_its_mac(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """The manual path out of #469, and the one that gets an entry too old
    to have a MAC onto the automatic one."""
    entry = _entry(hass)

    result = await _reconfigure(hass, entry, "10.0.0.77", _probe_result())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.77"
    assert entry.data[CONF_PORT] == 49155
    assert entry.data[CONF_MAC] == MAC
    # The probe re-mints when the device rejects the stored leaf, so what it
    # actually connected with is what gets kept.
    assert entry.data[CONF_LEAF_CERT_PEM] == "LEAF-CERT-2"
    assert entry.title == "Air conditioner (10.0.0.77)"


async def test_reconfigure_refuses_a_different_appliance(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """One digit wrong in the address would otherwise hand this entry's
    entity_ids and history to the neighbour that answered."""
    entry = _entry(hass)

    result = await _reconfigure(
        hass,
        entry,
        "10.0.0.78",
        _probe_result(device_key="UNIT-B", ocf_device_id="UNIT-B", serial="0J5F7CAT100999"),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_device"}
    assert entry.data[CONF_HOST] == "10.0.0.5"


async def test_reconfigure_follows_a_regenerated_device_id(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """A hard reset regenerates `di`; the serial is what says it's still the
    same unit -- the corroboration _resolve_identity uses on a poll."""
    entry = _entry(hass)

    result = await _reconfigure(
        hass, entry, "10.0.0.77", _probe_result(device_key="UNIT-A-REBORN", ocf_device_id=None)
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.77"


async def test_reconfigure_lets_a_host_keyed_entry_move(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """Issues #83/#189: a board with a placeholder serial and no usable `di`
    is registered against its address, so there is no identity to check and
    demanding one would strand exactly the entries that can't be re-added
    cleanly."""
    entry = _entry(
        hass,
        **{CONF_DEVICE_KEY: "10.0.0.5", CONF_SERIAL: "10.0.0.5"},
    )

    result = await _reconfigure(
        hass, entry, "10.0.0.77", _probe_result(device_key="10.0.0.77", serial="10.0.0.77")
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.77"


async def test_reconfigure_refuses_an_appliance_with_no_identity_at_all(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """A board with a placeholder serial and no `di` resolves to the address
    it was probed at (issues #83/#189). An entry that has a real identity has
    one to check, so agreeing with an address is not enough -- otherwise any
    such board would be accepted as any entry, the more so once an entry's
    stored key is itself an address it no longer lives at."""
    entry = _entry(hass)

    result = await _reconfigure(
        hass,
        entry,
        "10.0.0.77",
        _probe_result(device_key="10.0.0.77", ocf_device_id=None, serial="10.0.0.77"),
    )

    assert result["errors"] == {"base": "wrong_device"}
    assert entry.data[CONF_HOST] == "10.0.0.5"


async def test_a_host_keyed_entry_can_move_more_than_once(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    """Its key keeps naming the address it was created at -- _resolve_identity
    never re-keys a board reporting no `di` -- so the second move has nothing
    equal to compare and has to be recognized from the stored serial being an
    address instead."""
    entry = _entry(
        hass,
        **{CONF_HOST: "10.0.0.77", CONF_DEVICE_KEY: "10.0.0.5", CONF_SERIAL: "10.0.0.5"},
    )

    result = await _reconfigure(
        hass, entry, "10.0.0.99", _probe_result(device_key="10.0.0.99", serial="10.0.0.99")
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.99"
    # Still keyed on the address it was registered under: re-keying here
    # would orphan its rows, which is _resolve_identity's rule, not a gap.
    assert entry.data[CONF_DEVICE_KEY] == "10.0.0.5"


async def test_reconfigure_keeps_a_title_the_user_made_their_own(
    hass: HomeAssistant,
    enable_custom_integrations,
):
    entry = _entry(hass)
    hass.config_entries.async_update_entry(entry, title="Bedroom AC")

    await _reconfigure(hass, entry, "10.0.0.77", _probe_result())

    assert entry.title == "Bedroom AC"


# --------------------------------------------------------------------------
# Recording the MAC from a poll
# --------------------------------------------------------------------------


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> LocalThingsCoordinator:
    coordinator = LocalThingsCoordinator(hass, entry)
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="",
        name="",
        serial=None,
        device_id="unit-a",
        device_types=(),
        raw={"/oic/p": {}, "/oic/d": {}, "/oic/res": []},
    )
    return coordinator


def _resources(**overrides) -> dict[str, dict]:
    resources = dict(_load_device("dishwasher"))
    resources[WIRELESS_INFO_HREF] = dict(WIFI_REP)
    resources.update(overrides)
    return resources


async def test_a_poll_records_the_mac_and_publishes_the_connection(
    hass: HomeAssistant,
):
    entry = _entry(hass, **{CONF_DEVICE_KEY: "unit-a", CONF_SERIAL: "SERIAL-A"})
    coordinator = _coordinator(hass, entry)

    coordinator._run_discovery(_resources())
    await hass.async_block_till_done()

    assert entry.data[CONF_MAC] == MAC
    assert coordinator.device_info["connections"] == {(CONNECTION_NETWORK_MAC, MAC)}


async def test_an_entry_seeds_its_connection_before_the_first_poll(
    hass: HomeAssistant,
):
    """Entities register from the seeded DeviceInfo (issue #236), so a MAC
    already on the entry has to reach the registry without waiting."""
    coordinator = _coordinator(hass, _entry(hass, **{CONF_MAC: MAC}))

    assert coordinator.device_info["connections"] == {(CONNECTION_NETWORK_MAC, MAC)}


async def test_an_entry_with_no_mac_publishes_no_connection(hass: HomeAssistant):
    coordinator = _coordinator(hass, _entry(hass))

    assert coordinator.device_info["connections"] == set()


async def test_an_uncorroborated_appliance_contributes_no_mac(
    hass: HomeAssistant,
    caplog,
):
    """_resolve_identity refuses to re-key onto an appliance that answered
    at this address but can't corroborate the registered identity. Recording
    its MAC would hand it the entry anyway -- next time its lease moved, this
    entry would follow it (the reasoning CONF_OCF_DEVICE_ID gets in #435)."""
    entry = _entry(hass, **{CONF_DEVICE_KEY: "unit-a", CONF_SERIAL: "SERIAL-A"})
    coordinator = _coordinator(hass, entry)
    coordinator._identity = DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="",
        name="",
        serial=None,
        device_id="unit-b",
        device_types=(),
        raw={"/oic/p": {}, "/oic/d": {}, "/oic/res": []},
    )

    coordinator._run_discovery(_resources())
    await hass.async_block_till_done()

    assert "keeping the registered identity" in caplog.text
    assert CONF_MAC not in entry.data
    assert coordinator.device_info["connections"] == set()


async def test_an_offline_snapshot_load_still_learns_the_mac(
    hass: HomeAssistant,
):
    """The entry #469 actually arrives with is one already broken by a moved
    lease: it can't poll, so it loads from its snapshot (issue #295). That
    replay is this entry's own last reading of the device, so it is enough
    to learn the MAC from -- and the next sighting then repairs the host
    without the appliance ever having answered."""
    entry = _entry(hass, **{CONF_DEVICE_KEY: "unit-a", CONF_SERIAL: "SERIAL-A"})
    coordinator = _coordinator(hass, entry)

    coordinator._run_discovery(_resources(), from_snapshot=True)
    await hass.async_block_till_done()

    assert entry.data[CONF_MAC] == MAC
    # A replay never reached the device, so it still writes no identity key.
    assert entry.data[CONF_DEVICE_KEY] == "unit-a"


async def test_a_board_that_reports_no_mac_keeps_the_one_it_had(
    hass: HomeAssistant,
):
    """Half the corpus has no /wirelessinfo/vs/0 at all, and a single poll
    that misses the resource is not the device saying it moved."""
    entry = _entry(hass, **{CONF_MAC: MAC, CONF_DEVICE_KEY: "unit-a", CONF_SERIAL: "SERIAL-A"})
    coordinator = _coordinator(hass, entry)

    resources = _resources()
    del resources[WIRELESS_INFO_HREF]
    coordinator._run_discovery(resources)
    await hass.async_block_till_done()

    assert entry.data[CONF_MAC] == MAC
    assert coordinator.device_info["connections"] == {(CONNECTION_NETWORK_MAC, MAC)}


def test_the_manifest_asks_only_for_devices_home_assistant_already_knows():
    """A hostname or OUI matcher would fire for every Samsung device on the
    network; `registered_devices` is resolved against our own device rows,
    so a sighting only ever reaches us for an appliance already set up."""
    import json
    from pathlib import Path

    manifest = json.loads(
        (
            Path(__file__).resolve().parent.parent / "custom_components/localthings/manifest.json"
        ).read_text()
    )
    assert manifest["dhcp"] == [{"registered_devices": True}]
