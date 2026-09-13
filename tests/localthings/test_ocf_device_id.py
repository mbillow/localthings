"""Recording the OCF device UUID an authenticated read actually proved.

`CONF_DEVICE_KEY` is a registry key, resolved through a fallback chain
(`/oic/d`'s `di` -> `/oic/p`'s `pi` -> serialNum -> host), so it is not proof
of the OCF identity: on the boards that fall past the first branch it holds a
platform UUID shared by every logical device on one board, a vendor serial,
or an address. Anything that needs to know *which device answered* -- the
credential profiles in issue #435 -- needs the narrower value, so it is
stored separately.

Store-only for now. What a *changed* `di` should mean is the profiles'
decision; these tests fix the recording so that decision arrives with real
values already on entries.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_OCF_DEVICE_ID,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.localthings.registry.identity import (
    DeviceIdentity,
    ocf_device_key,
    proven_ocf_device_id,
)

from .conftest import (
    LEGACY_ENTRY_DATA,
    MOCK_DEVICE_KEY,
    MOCK_HOST,
    MOCK_SERIAL,
    _probe_result,
)
from .test_config_flow import _configure_host_then_ca
from .test_identity_migration import UUID_A, UUID_B, _reachable, _unreachable

_COORD = "custom_components.localthings.coordinator.LocalThingsCoordinator"
# OCF's nil UUID: firmware that never had one assigned reports it on every
# unit of the family, so it identifies nothing (issue #189).
NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _identity(*, device_id: str | None = None, platform_id: str | None = None) -> DeviceIdentity:
    return DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="AVT-WW-TP1-23-AXX500",
        name="Samsung AirPurifier",
        serial=None,
        device_id=device_id,
        platform_id=platform_id,
    )


# ---------------------------------------------------------------------------
# The helper: `di` only, never the rest of the key's chain
# ---------------------------------------------------------------------------


def test_reports_the_device_uuid_normalized() -> None:
    """Firmware that changes case between reads must not look like a
    different appliance -- same normalization the key already applies."""
    assert proven_ocf_device_id(_identity(device_id=UUID_A.upper())) == UUID_A


def test_does_not_fall_back_to_the_platform_uuid() -> None:
    """The load-bearing difference from `ocf_device_key`.

    `pi` is platform-scoped and shared by every logical device on one board,
    so it is a fine last resort for a stable registry key and no answer at
    all to "which device answered". A board reporting only `pi` must record
    no proven device id rather than record its platform's.
    """
    identity = _identity(platform_id=UUID_B)

    assert ocf_device_key(identity) == UUID_B
    assert proven_ocf_device_id(identity) is None


def test_rejects_the_nil_uuid() -> None:
    assert proven_ocf_device_id(_identity(device_id=NIL_UUID)) is None


def test_rejects_a_known_junk_identity_field() -> None:
    """A board flashed with 'Nothing(SVC)' in one identity field (issue #83)
    is not one to trust in another."""
    assert proven_ocf_device_id(_identity(device_id="Nothing(SVC)")) is None


def test_no_identity_at_all_is_not_an_answer() -> None:
    assert proven_ocf_device_id(None) is None


# ---------------------------------------------------------------------------
# Where it gets written
# ---------------------------------------------------------------------------


async def test_the_config_flow_records_it_when_the_probe_proved_one(
    hass: HomeAssistant, mock_probe, mock_coordinator_session
) -> None:
    """The probe already reads /oic/d, so a new entry starts with the value
    rather than waiting for its first poll to backfill it."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await _configure_host_then_ca(hass, result)
    await hass.async_block_till_done()

    assert result["data"][CONF_OCF_DEVICE_ID] == MOCK_DEVICE_KEY


async def test_a_probe_that_proved_nothing_leaves_the_key_absent(
    hass: HomeAssistant, mock_coordinator_session, mock_compatible
) -> None:
    """A board answering with no usable `di` still gets a registry key from
    further down the chain, but records no proven device id -- absent is the
    honest state, not a placeholder."""
    unproven = {**_probe_result(recognized=True), "ocf_device_id": None}
    with patch(
        "custom_components.localthings.config_flow._probe_and_validate",
        return_value=unproven,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await _configure_host_then_ca(hass, result)
        await hass.async_block_till_done()

    assert CONF_DEVICE_KEY in result["data"]
    assert CONF_OCF_DEVICE_ID not in result["data"]


def _entry_without_it(hass: HomeAssistant, **extra) -> MockConfigEntry:
    """An entry from before this field existed: keyed, but with nothing
    recorded about what the device proved."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **LEGACY_ENTRY_DATA,
            CONF_HOST: MOCK_HOST,
            CONF_SERIAL: MOCK_SERIAL,
            CONF_DEVICE_KEY: UUID_A,
            **extra,
        },
        unique_id=f"{DOMAIN}_{UUID_A}",
        version=4,
    )
    entry.add_to_hass(hass)
    return entry


async def test_an_existing_entry_backfills_it_on_the_first_poll(
    hass: HomeAssistant, fridge_resources
) -> None:
    """No migration writes this -- only the device can report it, and an
    entry can load entirely from its snapshot while the appliance is off
    (issue #295). The first authenticated read is where it lands."""
    entry = _entry_without_it(hass)

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_OCF_DEVICE_ID] == UUID_A


async def test_a_poll_reporting_no_usable_uuid_does_not_erase_a_stored_one(
    hass: HomeAssistant, fridge_resources
) -> None:
    """One read that comes back without a usable `di` is a gap in this
    poll's evidence, not a retraction of what an earlier authenticated read
    established."""
    entry = _entry_without_it(hass, **{CONF_OCF_DEVICE_ID: UUID_A})

    with _reachable(fridge_resources, None):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_OCF_DEVICE_ID] == UUID_A


async def test_an_uncorroborated_appliance_does_not_get_its_di_recorded(
    hass: HomeAssistant, fridge_resources
) -> None:
    """A different appliance answering at this address proves which device
    *it* is, not which device this entry is.

    `_resolve_identity` already refuses to re-key here, and deliberately
    withholds the intruder's serial too -- writing it would hand over the
    corroboration needed to win the next poll. The proven `di` has to be
    withheld on the same terms: it is the field issue #435's credential
    binding compares an authenticated session against, so recording an
    unadopted appliance's UUID would turn the mismatch it exists to catch
    into a match.
    """
    entry = _entry_without_it(
        hass,
        **{CONF_SERIAL: "SOME-OTHER-APPLIANCE", CONF_OCF_DEVICE_ID: UUID_A},
    )

    with _reachable(fridge_resources, UUID_B):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert entry.data[CONF_OCF_DEVICE_ID] == UUID_A


async def test_a_snapshot_replay_proves_nothing_and_writes_nothing(
    hass: HomeAssistant, fridge_resources, hass_storage
) -> None:
    """Replaying a stored snapshot never reached the device, so it has no
    standing to say which one answered -- the same reason it must not write
    a device key (issue #295).

    The snapshot restores `_identity`, `di` included, so a value is sitting
    right there for an unguarded replay to write as though a live read had
    proved it. Clearing the field after banking the snapshot is what makes
    the guard, rather than the leftover value, the thing under test.
    """
    entry = _entry_without_it(hass)

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.data[CONF_OCF_DEVICE_ID] == UUID_A
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_OCF_DEVICE_ID}
    )

    with _unreachable():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert CONF_OCF_DEVICE_ID not in entry.data


async def test_it_is_recorded_without_being_used_as_the_registry_key(
    hass: HomeAssistant, fridge_resources
) -> None:
    """Store-only: recording what the device proved must not disturb the key
    the user's entities and history already hang off."""
    entry = _entry_without_it(hass)

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert coordinator.device_key == UUID_A
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
