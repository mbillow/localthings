"""Moving an existing install onto the OCF device UUID (issue #381).

This migration can't finish inside `async_migrate_entry` -- the UUID is
only readable from the appliance, and an entry can load from its snapshot
while that appliance is off (issue #295) -- so the coordinator adopts it
on the first live poll, rewriting both registries and the entry's
unique_id together.

That makes it the riskiest migration here: it rewrites the identity of
rows a user's automations, history and areas hang off. These tests are
organised around what must not break rather than around the functions
involved. Statistics are covered separately, against a real recorder, in
tests/test_rekey_statistics_end_to_end.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import (
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_SERIAL,
    DOMAIN,
)
from custom_components.localthings.registry.identity import DeviceIdentity

from .conftest import (
    LEGACY_ENTRY_DATA,
    MOCK_HOST,
    MOCK_SERIAL,
    entry_has_identifier,
    link_to_parent,
)

_COORD = "custom_components.localthings.coordinator.LocalThingsCoordinator"

# The two purifiers from issue #381: one serialNum, two device UUIDs.
SHARED_SERIAL = "BS7SP9AW400114A"
UUID_A = "ccfd73b3-aeb4-792a-1100-68f06f5d603b"
UUID_B = "3771f8bf-c184-3a2d-d885-e4c9818736d2"


@contextmanager
def _reachable(resources: dict, device_id: str | None):
    """A device that answers a poll, reporting `device_id` as its /oic/d
    `di` -- which `_connect_session` is what normally reads, so a test that
    patches it out otherwise leaves `_identity` None (indistinguishable
    from firmware that reports no UUID at all)."""

    def _connect(self) -> None:
        self._identity = (
            None
            if device_id is None
            else DeviceIdentity(
                manufacturer="Samsung Electronics",
                model="AVT-WW-TP1-23-AXX500",
                name="Samsung AirPurifier",
                serial=None,
                device_id=device_id,
            )
        )

    with (
        patch(f"{_COORD}._connect_session", _connect),
        patch(f"{_COORD}._poll_once", return_value=resources),
        patch(f"{_COORD}._close_session"),
    ):
        yield


@contextmanager
def _unreachable():
    with (
        patch(f"{_COORD}._connect_session"),
        patch(f"{_COORD}._poll_once", side_effect=OSError("device offline")),
        patch(f"{_COORD}._close_session"),
    ):
        yield


def _entry(
    hass: HomeAssistant,
    *,
    version: int,
    key: str,
    serial: str | None = None,
    device_key: str | None = None,
    host: str = MOCK_HOST,
) -> MockConfigEntry:
    """An entry as it sits on disk at `version`. Pre-v4 entries carry no
    CONF_DEVICE_KEY at all -- that absence is what tells the coordinator it
    is looking at an entry that has never adopted a UUID."""
    data = {**LEGACY_ENTRY_DATA, CONF_HOST: host}
    if version >= 2:
        data[CONF_SERIAL] = serial if serial is not None else key
    if device_key is not None:
        data[CONF_DEVICE_KEY] = device_key
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        unique_id=f"{DOMAIN}_{key}",
        version=version,
    )
    entry.add_to_hass(hass)
    return entry


def _reporting_serial(resources: dict, serial: str) -> dict:
    """`resources` with the serialNum the device reports swapped out, so a
    test can pair a fixture with the identity its scenario implies."""
    info = dict(resources["/information/vs/0"])
    info["x.com.samsung.da.serialNum"] = serial
    return {**resources, "/information/vs/0": info}


def _device_identifiers(hass: HomeAssistant, device_id: str) -> set[tuple[str, str]]:
    """This device row's identifiers, asserting the row still exists.

    `dev_reg.async_get` returns `DeviceEntry | None`, so reading through it
    directly would crash with an AttributeError on a row the re-key
    wrongly removed instead of failing the assertion that says so.
    """
    row = dr.async_get(hass).async_get(device_id)
    assert row is not None
    return row.identifiers


def _entity_unique_id(hass: HomeAssistant, entity_id: str) -> str:
    """This entity row's unique_id, asserting the row still exists."""
    row = er.async_get(hass).async_get(entity_id)
    assert row is not None
    return row.unique_id


def _seed_registry(hass: HomeAssistant, entry: MockConfigEntry, key: str, **entity_kwargs):
    """A device and one entity keyed on `key`, as a running install has."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, key)},
        name=f"Samsung Air Purifier ({key})",
    )
    entity = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{key}_connection_mode",
        config_entry=entry,
        device_id=device.id,
        **entity_kwargs,
    )
    return device, entity


# ---------------------------------------------------------------------------
# The upgrade itself
# ---------------------------------------------------------------------------


async def test_v3_entry_moves_onto_the_device_uuid_keeping_its_entity_ids(
    hass: HomeAssistant, fridge_resources
) -> None:
    """The migration promise for an existing user, asserted end to end: all
    three permanent places move together, and the entity_id doesn't."""
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    device, existing = _seed_registry(
        hass, entry, MOCK_SERIAL, suggested_object_id="kitchen_purifier_connection"
    )

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 5
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    # The serial is kept alongside the key, not replaced by it: it is what
    # corroborates a later change of UUID.
    assert entry.data[CONF_SERIAL] == MOCK_SERIAL
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"

    rekeyed_device = dr.async_get(hass).async_get(device.id)
    assert rekeyed_device is not None
    assert rekeyed_device.identifiers == {(DOMAIN, UUID_A)}
    kept = er.async_get(hass).async_get(existing.entity_id)
    assert kept is not None
    assert kept.entity_id == "sensor.kitchen_purifier_connection"
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    # Nothing left behind on the old key.
    assert not entry_has_identifier(hass, entry, (DOMAIN, MOCK_SERIAL))


async def test_the_oldest_install_walks_all_the_way_from_v1(
    hass: HomeAssistant, fridge_resources
) -> None:
    """A v1 entry -- no stored identity at all, from before issue #236 --
    walks v1 -> v2 -> v3 -> v4 -> v5 and then adopts the UUID on its first
    poll.

    The oldest installs take the longest path, and each step rewrites what
    the next one reads, so the chain is worth pinning as one journey rather
    than trusting the individual steps to compose.
    """
    entry = _entry(hass, version=1, key=MOCK_SERIAL)
    device, existing = _seed_registry(
        hass, entry, MOCK_SERIAL, suggested_object_id="old_install_connection"
    )

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 5
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
    kept = er.async_get(hass).async_get(existing.entity_id)
    assert kept is not None
    assert kept.entity_id == "sensor.old_install_connection"
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    assert _device_identifiers(hass, device.id) == {(DOMAIN, UUID_A)}


async def test_a_host_keyed_entry_adopts_a_real_identity(
    hass: HomeAssistant, fridge_resources
) -> None:
    """A placeholder-serial board (issues #83/#189) was keyed on its IP,
    which is an address rather than an identity -- a new DHCP lease silently
    makes it someone else's. Such an entry never made an identity claim to
    defend, so a real UUID is adopted without needing the serial to
    corroborate it; requiring corroboration would strand exactly these
    boards, since their serial resolves to the host and can never match."""
    entry = _entry(hass, version=3, key=MOCK_HOST)
    device, _ = _seed_registry(hass, entry, MOCK_HOST)

    with _reachable(fridge_resources, UUID_B):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_DEVICE_KEY] == UUID_B
    assert _device_identifiers(hass, device.id) == {(DOMAIN, UUID_B)}


async def test_the_two_units_from_the_issue_migrate_to_separate_identities(
    hass: HomeAssistant, fridge_resources
) -> None:
    """Issue #381's actual install: two entries whose stored identity is the
    same shared serial. Before this, they could not both exist. Migrating
    them must give each its own key rather than collapsing them again --
    including their entity unique_ids, which is where issue #83's Bug 4
    silently dropped the second unit's entities even once its entry existed.
    """
    # Both units report the shared serial, as the real ones do -- so the
    # entry's stored identity still matches what the device says, and only
    # the UUID separates them.
    resources = _reporting_serial(fridge_resources, SHARED_SERIAL)
    first = _entry(hass, version=3, key=SHARED_SERIAL, host="192.168.0.3")
    _, first_entity = _seed_registry(hass, first, SHARED_SERIAL, suggested_object_id="purifier_a")

    with _reachable(resources, UUID_A):
        await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

    # Added only now: setting up the first entry loads the integration, which
    # brings up every entry already registered -- so a second one created up
    # front would come up inside the first one's patched identity and adopt
    # its UUID, which is the collision this test exists to disprove.
    second = _entry(hass, version=3, key=SHARED_SERIAL, host="192.168.0.14")
    _, second_entity = _seed_registry(hass, second, SHARED_SERIAL, suggested_object_id="purifier_b")

    with _reachable(resources, UUID_B):
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    assert first.data[CONF_DEVICE_KEY] == UUID_A
    assert second.data[CONF_DEVICE_KEY] == UUID_B
    assert first.unique_id != second.unique_id

    assert _entity_unique_id(hass, first_entity.entity_id) == (f"{DOMAIN}_{UUID_A}_connection_mode")
    assert _entity_unique_id(hass, second_entity.entity_id) == (
        f"{DOMAIN}_{UUID_B}_connection_mode"
    )


# ---------------------------------------------------------------------------
# What the user must not lose
# ---------------------------------------------------------------------------


async def test_every_user_customization_on_the_row_survives(
    hass: HomeAssistant, fridge_resources
) -> None:
    """Re-keying rewrites the registry row in place rather than replacing
    it, which is the whole reason to do it this way -- so everything the
    user attached to that row rides along. A rename, an area, an icon
    override and a deliberate hide are each things they would have to redo
    by hand if the row were recreated instead.
    """
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    ent_reg = er.async_get(hass)
    device, existing = _seed_registry(
        hass, entry, MOCK_SERIAL, suggested_object_id="kitchen_purifier_connection"
    )
    dr.async_get(hass).async_update_device(device.id, area_id="kitchen")
    ent_reg.async_update_entity(
        existing.entity_id,
        name="Purifier link",
        icon="mdi:air-filter",
        area_id="kitchen",
        hidden_by=er.RegistryEntryHider.USER,
    )

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    kept = ent_reg.async_get(existing.entity_id)
    assert kept is not None
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    assert kept.name == "Purifier link"
    assert kept.icon == "mdi:air-filter"
    assert kept.area_id == "kitchen"
    assert kept.hidden_by is er.RegistryEntryHider.USER
    rekeyed_device = dr.async_get(hass).async_get(device.id)
    assert rekeyed_device is not None
    assert rekeyed_device.area_id == "kitchen"


async def test_a_composite_appliance_keeps_its_subdevice_links(
    hass: HomeAssistant, fridge_resources
) -> None:
    """A composite appliance (issue #177) registers one device per logical
    subdevice, keyed f"{key}_{subdevice}" and linked via_device to the
    master's bare key. Rewriting only the exact-match identifier would
    strand every sibling under a via_device pointing at a device that no
    longer exists, collapsing the user's device tree."""
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    dev_reg = dr.async_get(hass)
    master, _ = _seed_registry(hass, entry, MOCK_SERIAL)
    sub = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MOCK_SERIAL}_subdevice_1")},
        **link_to_parent(master),
    )
    assert sub.via_device_id == master.id

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert _device_identifiers(hass, master.id) == {(DOMAIN, UUID_A)}
    rekeyed_sub = dev_reg.async_get(sub.id)
    # isinstance, not `is not None`: HA 2026.9's async_get can also return a
    # ChildDeviceEntry, which carries no via_device_id.
    assert isinstance(rekeyed_sub, dr.DeviceEntry)
    assert rekeyed_sub.identifiers == {(DOMAIN, f"{UUID_A}_subdevice_1")}
    # Still the same parent row, so the device tree the user sees is intact.
    assert rekeyed_sub.via_device_id == master.id


async def test_a_re_key_never_touches_another_entrys_rows(hass: HomeAssistant) -> None:
    """The rewrite is scoped to one config entry's own registry rows.

    Issue #381's install is the case that makes this sharp: *both* entries
    are keyed on the same shared serial, so both have registry rows under
    the identical old key, and they migrate one at a time. An unscoped
    rewrite -- matching on the key prefix alone -- would sweep up the other
    appliance's device and entities and hand them to the first one to
    migrate, which is the worst outcome this change could have.

    The two entries hold *different* entities under that one shared prefix
    (HA's registry won't let two rows share a unique_id, which is issue
    #83's Bug 4 in the first place), so only the config-entry scoping can
    tell them apart -- prefix matching alone cannot.

    Calls rekey_entry directly so the scoping is what's under test, rather
    than the coordinator's decision about whether to call it at all.
    """
    from custom_components.localthings.rekey import rekey_entry

    migrating = _entry(hass, version=3, key=SHARED_SERIAL, host="192.168.0.3")
    bystander = _entry(hass, version=3, key=SHARED_SERIAL, host="192.168.0.14")
    ent_reg = er.async_get(hass)
    moved = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{SHARED_SERIAL}_connection_mode", config_entry=migrating
    )
    stays = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{SHARED_SERIAL}_power", config_entry=bystander
    )

    rekey_entry(hass, migrating, SHARED_SERIAL, UUID_A)

    assert _entity_unique_id(hass, moved.entity_id) == f"{DOMAIN}_{UUID_A}_connection_mode"
    # The other appliance is still on the shared serial, waiting its turn.
    assert _entity_unique_id(hass, stays.entity_id) == f"{DOMAIN}_{SHARED_SERIAL}_power"
    assert bystander.unique_id == f"{DOMAIN}_{SHARED_SERIAL}"


async def test_a_re_key_stops_at_the_key_boundary(hass: HomeAssistant) -> None:
    """Matching is on the whole key or the key plus a separator, never a
    bare prefix. Serial numbers of one model are routinely prefixes of each
    other, so a naive startswith would drag a *different* appliance's rows
    along -- and the identifiers it would rewrite them to are nonsense."""
    from custom_components.localthings.rekey import rekey_entry

    entry = _entry(hass, version=3, key="TEST-SERIAL")
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    target = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "TEST-SERIAL")}
    )
    # Same config entry, so scoping can't save this one -- only the boundary.
    neighbour = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "TEST-SERIAL-0000")}
    )
    neighbour_entity = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_TEST-SERIAL-0000_connection_mode", config_entry=entry
    )

    rekey_entry(hass, entry, "TEST-SERIAL", UUID_A)

    assert _device_identifiers(hass, target.id) == {(DOMAIN, UUID_A)}
    assert _device_identifiers(hass, neighbour.id) == {(DOMAIN, "TEST-SERIAL-0000")}
    assert _entity_unique_id(hass, neighbour_entity.entity_id) == (
        f"{DOMAIN}_TEST-SERIAL-0000_connection_mode"
    )


# ---------------------------------------------------------------------------
# Stability: upgrading twice, or offline, must not churn
# ---------------------------------------------------------------------------


async def test_upgrading_while_the_appliance_is_off_changes_nothing(
    hass: HomeAssistant, fridge_resources, hass_storage
) -> None:
    """The case a large share of users will actually hit: HA restarts onto
    the new release while the appliance is unplugged or asleep.

    The entry comes up from its snapshot (issue #295), which never reached
    the device -- so it has no standing to claim an identity. It must load
    under the key its registry rows already carry and write nothing, or the
    real UUID would later look like a *changed* identity to defend against
    rather than the one-time adoption it is.
    """
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    _, existing = _seed_registry(hass, entry, MOCK_SERIAL)

    # Bank a snapshot from a run on the old release, then take it down.
    with _reachable(fridge_resources, None):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data[CONF_DEVICE_KEY] == MOCK_SERIAL

    # Now the appliance is off. Simulate the pre-v4 shape the upgrade finds.
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_DEVICE_KEY}, version=3
    )

    with _unreachable():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.device_key == MOCK_SERIAL
    # No key claimed, and the registry is exactly as it was.
    assert CONF_DEVICE_KEY not in entry.data
    assert entry.unique_id == f"{DOMAIN}_{MOCK_SERIAL}"
    assert _entity_unique_id(hass, existing.entity_id) == (
        f"{DOMAIN}_{MOCK_SERIAL}_connection_mode"
    )


async def test_an_offline_load_never_rewrites_the_registry(
    hass: HomeAssistant, fridge_resources, hass_storage
) -> None:
    """A snapshot replay must not re-key on the strength of what the
    snapshot says, because that is last run's answer rather than the
    device's.

    Modelled on a placeholder-serial board (issues #83/#189), which is
    where the two can genuinely disagree: the entry is keyed on its address
    because the board reports no usable serial, while the snapshot banked
    whatever serial the polled resources carried. A replay that trusted the
    snapshot would rewrite every registry row onto that serial -- without
    the appliance having been reachable at any point -- and would then
    freeze the answer into CONF_DEVICE_KEY, so the real UUID could never be
    adopted afterwards.
    """
    entry = _entry(hass, version=3, key=MOCK_HOST)

    with _reachable(fridge_resources, None):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    # Back to the pre-v4 shape the upgrade finds, still keyed on the address.
    hass.config_entries.async_update_entry(
        entry,
        data={
            **{k: v for k, v in entry.data.items() if k != CONF_DEVICE_KEY},
            CONF_SERIAL: MOCK_HOST,
        },
        unique_id=f"{DOMAIN}_{MOCK_HOST}",
        version=3,
    )
    device, existing = _seed_registry(hass, entry, MOCK_HOST)

    with _unreachable():
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.device_key == MOCK_HOST
    assert CONF_DEVICE_KEY not in entry.data
    assert entry.unique_id == f"{DOMAIN}_{MOCK_HOST}"
    assert _device_identifiers(hass, device.id) == {(DOMAIN, MOCK_HOST)}
    assert _entity_unique_id(hass, existing.entity_id) == (f"{DOMAIN}_{MOCK_HOST}_connection_mode")

    # And the deferred adoption still works once the appliance answers --
    # the offline load left nothing frozen behind it.
    with _reachable(fridge_resources, UUID_A):
        coordinator._connect_session()
        coordinator._run_discovery(fridge_resources)

    assert coordinator.device_key == UUID_A
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert _entity_unique_id(hass, existing.entity_id) == (f"{DOMAIN}_{UUID_A}_connection_mode")


async def test_the_appliance_coming_back_completes_the_upgrade(
    hass: HomeAssistant, fridge_resources
) -> None:
    """The other half of the offline case: the deferred adoption is not
    abandoned, it just waits. Once the device answers, the same re-key runs
    and the entry finishes its upgrade."""
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    _, existing = _seed_registry(hass, entry, MOCK_SERIAL)

    with _reachable(fridge_resources, None):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data[CONF_DEVICE_KEY] == MOCK_SERIAL

    # The device is reachable again, now reporting its UUID.
    coordinator = hass.data[DOMAIN][entry.entry_id]
    with _reachable(fridge_resources, UUID_A):
        coordinator._connect_session()
        coordinator._run_discovery(fridge_resources)

    assert coordinator.device_key == UUID_A
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
    assert _entity_unique_id(hass, existing.entity_id) == (f"{DOMAIN}_{UUID_A}_connection_mode")


async def test_restarting_after_the_upgrade_is_a_no_op(
    hass: HomeAssistant, fridge_resources
) -> None:
    """Every restart re-runs discovery, so the adoption path runs again on
    an entry that has already moved. It must recognise its own work and do
    nothing -- a re-key that fired every boot would churn the registry
    forever."""
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    _, existing = _seed_registry(hass, entry, MOCK_SERIAL)

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        after_first = dict(entry.data)
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert dict(entry.data) == after_first
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
    kept = er.async_get(hass).async_get(existing.entity_id)
    assert kept is not None
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    # Exactly one device, not a duplicate alongside it.
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1


async def test_an_already_migrated_entry_is_left_alone(
    hass: HomeAssistant, fridge_resources
) -> None:
    """A v5 entry created by the current config flow has nothing to migrate
    and nothing to re-key -- it was minted on its UUID."""
    entry = _entry(hass, version=5, key=UUID_A, serial=MOCK_SERIAL, device_key=UUID_A)
    device, existing = _seed_registry(hass, entry, UUID_A)

    with _reachable(fridge_resources, UUID_A):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 5
    assert entry.data[CONF_DEVICE_KEY] == UUID_A
    assert _device_identifiers(hass, device.id) == {(DOMAIN, UUID_A)}
    assert _entity_unique_id(hass, existing.entity_id) == (f"{DOMAIN}_{UUID_A}_connection_mode")


# ---------------------------------------------------------------------------
# Guarding the identity once it has moved
# ---------------------------------------------------------------------------


async def test_a_poll_that_reads_no_uuid_does_not_demote_a_keyed_entry(
    hass: HomeAssistant, fridge_resources
) -> None:
    """The device saying nothing is not the device saying something
    different. A reconnect that can't read /oic/d (a timeout, a firmware
    hiccup) must leave the key alone -- demoting back onto the serial would
    re-key every entity the user has for the duration of an outage, and
    re-key them all back afterwards."""
    entry = _entry(hass, version=4, key=UUID_A, serial=MOCK_SERIAL, device_key=UUID_A)

    with _reachable(fridge_resources, None):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.device_key == UUID_A
    assert entry.data[CONF_DEVICE_KEY] == UUID_A


async def test_a_rotated_uuid_on_the_same_serial_is_followed(
    hass: HomeAssistant, fridge_resources
) -> None:
    """OCF permits a hard factory reset to regenerate `di`. The serialNum is
    what tells that apart from a different appliance moving onto the
    address, and following it keeps the user's history rather than stranding
    it on a UUID the device will never report again."""
    entry = _entry(hass, version=4, key=UUID_A, serial=MOCK_SERIAL, device_key=UUID_A)
    device, existing = _seed_registry(hass, entry, UUID_A)

    # fridge_resources reports MOCK_SERIAL, matching what the entry stored.
    with _reachable(fridge_resources, UUID_B):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_DEVICE_KEY] == UUID_B
    assert _device_identifiers(hass, device.id) == {(DOMAIN, UUID_B)}
    assert _entity_unique_id(hass, existing.entity_id) == (f"{DOMAIN}_{UUID_B}_connection_mode")


async def test_a_different_appliance_on_the_same_address_keeps_the_registered_identity(
    hass: HomeAssistant, fridge_resources
) -> None:
    """Neither the UUID nor the serial matches what this entry was
    registered with, so this is a different appliance answering at this
    address -- not a reset of the registered one. Re-keying here would hand
    one appliance's entities, history and automations to another; re-adding
    is the user's call."""
    entry = _entry(hass, version=4, key=UUID_A, serial="SOME-OTHER-APPLIANCE", device_key=UUID_A)
    device, _ = _seed_registry(hass, entry, UUID_A)

    with _reachable(fridge_resources, UUID_B):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.data[CONF_DEVICE_KEY] == UUID_A
        assert _device_identifiers(hass, device.id) == {(DOMAIN, UUID_A)}
        # The rejected appliance's serial is not written either. The serial
        # is what corroborates a later change of key, so adopting it here
        # would hand the intruder exactly the corroboration it needs to win
        # the *next* poll -- defending the identity once and then
        # surrendering it on the following cycle.
        assert entry.data[CONF_SERIAL] == "SOME-OTHER-APPLIANCE"

        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator._run_discovery(fridge_resources)

        assert coordinator.device_key == UUID_A
        assert entry.data[CONF_DEVICE_KEY] == UUID_A
        assert entry.data[CONF_SERIAL] == "SOME-OTHER-APPLIANCE"


async def test_a_pre_v4_entry_defends_itself_against_a_different_appliance(
    hass: HomeAssistant, fridge_resources
) -> None:
    """The "same IP, different appliance" guard applies to an entry that has
    not migrated yet, exactly as it does to one that has.

    A pre-v4 entry is the population this change exists to move, but it is
    also the population that has been running longest -- so it is the last
    one that should hand its entity_ids, history and automations to an
    appliance that merely happens to have taken over its address. Adoption
    is the migration's job only when the identity is corroborated.
    """
    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    device, existing = _seed_registry(hass, entry, MOCK_SERIAL)

    # Neither the serial nor (therefore) the UUID belongs to the registered
    # appliance.
    resources = _reporting_serial(fridge_resources, "SOME-OTHER-APPLIANCE")
    with _reachable(resources, UUID_B):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.device_key == MOCK_SERIAL
    assert entry.data[CONF_DEVICE_KEY] == MOCK_SERIAL
    assert entry.data[CONF_SERIAL] == MOCK_SERIAL
    assert entry.unique_id == f"{DOMAIN}_{MOCK_SERIAL}"
    assert _device_identifiers(hass, device.id) == {(DOMAIN, MOCK_SERIAL)}
    assert _entity_unique_id(hass, existing.entity_id) == (
        f"{DOMAIN}_{MOCK_SERIAL}_connection_mode"
    )


# ---------------------------------------------------------------------------
# rekey_entry's own contract
# ---------------------------------------------------------------------------


async def test_rekey_is_idempotent(hass: HomeAssistant) -> None:
    """Safe to attempt on every poll rather than having to track whether it
    has already run -- a second call finds nothing under the old key.

    Calls rekey_entry directly: what it leaves behind is the contract, and
    going through a setup would let the platforms re-adding their entities
    hide a row that had in fact been orphaned.
    """
    from custom_components.localthings.rekey import rekey_entry

    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    _, existing = _seed_registry(hass, entry, MOCK_SERIAL)

    rekey_entry(hass, entry, MOCK_SERIAL, UUID_A)
    rekey_entry(hass, entry, MOCK_SERIAL, UUID_A)

    kept = er.async_get(hass).async_get(existing.entity_id)
    assert kept is not None
    assert kept.unique_id == f"{DOMAIN}_{UUID_A}_connection_mode"
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"


async def test_rekey_removes_a_stale_row_rather_than_colliding(hass: HomeAssistant) -> None:
    """Where the destination key is already taken, the old-key row is the
    dead one -- unavailable since whichever restart created the split -- so
    it goes rather than being rewritten onto a key that exists. Same rule
    the #236 repair has always applied, now on the identity move."""
    from custom_components.localthings.rekey import rekey_entry

    entry = _entry(hass, version=3, key=MOCK_SERIAL)
    ent_reg = er.async_get(hass)
    live = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{UUID_A}_connection_mode", config_entry=entry
    )
    stale = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{MOCK_SERIAL}_connection_mode", config_entry=entry
    )
    assert stale.entity_id != live.entity_id

    rekey_entry(hass, entry, MOCK_SERIAL, UUID_A)

    assert ent_reg.async_get(stale.entity_id) is None
    assert ent_reg.async_get(live.entity_id) is not None


async def test_rekey_to_the_same_key_does_nothing(hass: HomeAssistant) -> None:
    """The no-op guard that lets callers pass whatever they resolved without
    checking first."""
    from custom_components.localthings.rekey import rekey_entry

    entry = _entry(hass, version=4, key=UUID_A, device_key=UUID_A)
    _, existing = _seed_registry(hass, entry, UUID_A)

    rekey_entry(hass, entry, UUID_A, UUID_A)

    assert _entity_unique_id(hass, existing.entity_id) == (f"{DOMAIN}_{UUID_A}_connection_mode")
    assert entry.unique_id == f"{DOMAIN}_{UUID_A}"
