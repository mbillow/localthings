"""The v2 -> v3 entry migration that relabels particulate statistics.

Dust/FineDust/SuperFineDust gained a pm10/pm25/pm1 device_class and a
µg/m³ unit (issue #325) after having recorded long-term statistics with no
unit at all. Home Assistant treats that as a unit change it cannot convert
and *suppresses statistics generation* for the entity until a human
resolves the repair, so the metadata is corrected during migration instead.

Only the metadata row is touched, never the recorded values -- the readings
were always µg/m³, so there is nothing to convert.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import async_migrate_entry
from custom_components.localthings.const import CONF_DEVICE_TYPE, DOMAIN

from .conftest import ENTRY_DATA, MOCK_SERIAL

RELABEL = "homeassistant.components.recorder.statistics.async_update_statistics_metadata"


@pytest.fixture(autouse=True)
def _recorder_loaded(hass: HomeAssistant):
    """Most tests here assume a normal install, where after_dependencies has
    pulled the recorder in. The deferral test below undoes it."""
    hass.config.components.add("recorder")
    return hass


def _entry(hass: HomeAssistant, device_type: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**ENTRY_DATA, CONF_DEVICE_TYPE: device_type},
        unique_id=f"{DOMAIN}_{MOCK_SERIAL}",
        version=2,
    )
    entry.add_to_hass(hass)
    return entry


def _add_sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str, **kwargs):
    return er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{MOCK_SERIAL}_{key}",
        config_entry=entry,
        **kwargs,
    )


async def test_relabels_every_particulate_sensor(hass: HomeAssistant) -> None:
    entry = _entry(hass, "air_purifier")
    expected = {
        _add_sensor(hass, entry, key).entity_id for key in ("dust", "fine_dust", "super_fine_dust")
    }

    with patch(RELABEL, autospec=True) as relabel:
        assert await async_migrate_entry(hass, entry) is True

    assert {call.args[1] for call in relabel.call_args_list} == expected
    for call in relabel.call_args_list:
        assert call.kwargs["new_unit_of_measurement"] == CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
        # µg/m³ has a converter, so the class must be named, not None --
        # passing neither is deprecated and breaks in HA Core 2026.11.
        assert call.kwargs["new_unit_class"] == "concentration"
    assert entry.version == 5


async def test_leaves_other_sensors_on_the_same_device_alone(hass: HomeAssistant) -> None:
    """Odor/CleanLevel/CO2 share the resource but keep the units they had."""
    entry = _entry(hass, "air_monitor")
    dust = _add_sensor(hass, entry, "dust")
    for key in ("odor", "clean_level", "co2", "dustbag_usage", "dustbin_auto_close"):
        _add_sensor(hass, entry, key)

    with patch(RELABEL, autospec=True) as relabel:
        assert await async_migrate_entry(hass, entry) is True

    assert [call.args[1] for call in relabel.call_args_list] == [dust.entity_id]


async def test_skips_families_that_did_not_gain_the_unit(hass: HomeAssistant) -> None:
    """range_hood and airconditioner still declare no unit for their
    identically-named sensors. Relabelling their statistics would assert a
    unit those entities don't report -- creating the very mismatch this
    migration exists to prevent."""
    for device_type in ("range_hood", "airconditioner"):
        entry = _entry(hass, device_type)
        _add_sensor(hass, entry, "dust")
        _add_sensor(hass, entry, "fine_dust")

        with patch(RELABEL, autospec=True) as relabel:
            assert await async_migrate_entry(hass, entry) is True

        assert relabel.call_args_list == [], device_type
        assert entry.version == 5


async def test_defers_rather_than_consuming_the_migration_without_the_recorder(
    hass: HomeAssistant,
) -> None:
    """A boot where the recorder didn't come up must not burn the one-shot
    migration -- doing so would leave the statistics suppressed for good.
    The entry stays on v2 so the next start retries."""
    entry = _entry(hass, "air_purifier")
    _add_sensor(hass, entry, "dust")

    hass.config.components.remove("recorder")
    with patch(RELABEL, autospec=True) as relabel:
        assert await async_migrate_entry(hass, entry) is True

    assert relabel.call_args_list == []
    assert entry.version == 2

    # ...and the retry lands once the recorder is there.
    hass.config.components.add("recorder")
    with patch(RELABEL, autospec=True) as relabel:
        assert await async_migrate_entry(hass, entry) is True

    assert len(relabel.call_args_list) == 1
    assert entry.version == 5


async def test_omits_unit_class_on_an_older_home_assistant(hass: HomeAssistant) -> None:
    """`new_unit_class` only exists from HA 2025.11, and hacs.json still
    declares 2025.1 as the minimum. Passing it to the older signature is a
    TypeError out of async_migrate_entry, which fails the whole entry -- so
    the kwarg is feature-detected rather than assumed.

    Stands in for an older HA by patching in that exact signature; the
    autospec'd tests above cover the modern one.
    """
    entry = _entry(hass, "air_purifier")
    _add_sensor(hass, entry, "dust")

    seen: list[dict] = []

    def old_signature(hass, statistic_id, *, new_statistic_id=None, **kwargs):
        seen.append(kwargs)

    with patch(RELABEL, old_signature):
        assert await async_migrate_entry(hass, entry) is True

    assert seen == [{"new_unit_of_measurement": CONCENTRATION_MICROGRAMS_PER_CUBIC_METER}]
    assert entry.version == 5


async def test_a_relabel_failure_never_fails_the_entry(hass: HomeAssistant) -> None:
    """Relabelling is a convenience -- without it the user gets HA's own
    units_changed repair, which is where they were before. An older HA whose
    async_update_statistics_metadata has a different signature, or any other
    recorder-side surprise, must not cost them the integration."""
    entry = _entry(hass, "air_purifier")
    _add_sensor(hass, entry, "dust")

    with patch(RELABEL, autospec=True, side_effect=TypeError("older HA signature")):
        assert await async_migrate_entry(hass, entry) is True

    assert entry.version == 5


async def test_follows_a_renamed_entity_rather_than_rebuilding_its_id(
    hass: HomeAssistant,
) -> None:
    """statistic_id is the entity_id, which the user can rename. Matching the
    unique_id tail and reading entity_id back off the registry is what keeps
    this correct for a renamed sensor -- reconstructing an entity_id from the
    descriptor key would relabel a statistic nobody is recording."""
    entry = _entry(hass, "air_purifier")
    renamed = _add_sensor(hass, entry, "dust", suggested_object_id="living_room_pm10")
    assert renamed.entity_id == "sensor.living_room_pm10"

    with patch(RELABEL, autospec=True) as relabel:
        assert await async_migrate_entry(hass, entry) is True

    assert [call.args[1] for call in relabel.call_args_list] == ["sensor.living_room_pm10"]


async def test_matches_subdevice_prefixed_and_instanced_keys(hass: HomeAssistant) -> None:
    """_key() can prefix a subdevice and append an instance number, so the
    match is on the tail rather than the whole unique_id. The instance form
    is `_<n>` (discovery.instance_suffix), not a bare digit."""
    entry = _entry(hass, "air_purifier")
    ent_reg = er.async_get(hass)
    for unique_suffix in ("indoor_0_dust", "fine_dust_1", "super_fine_dust"):
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{DOMAIN}_{MOCK_SERIAL}_{unique_suffix}",
            config_entry=entry,
        )
    # Near-misses that must not match.
    for unique_suffix in ("dustbag_full", "dustbin_auto_close", "dust_filter_reset"):
        ent_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{DOMAIN}_{MOCK_SERIAL}_{unique_suffix}",
            config_entry=entry,
        )

    with patch(RELABEL, autospec=True) as relabel:
        assert await async_migrate_entry(hass, entry) is True

    assert len(relabel.call_args_list) == 3


async def test_a_fresh_entry_starts_at_the_migrated_version(hass: HomeAssistant) -> None:
    """A newly created entry has nothing either migration step needs to do --
    no statistics to relabel, and the probe already resolved its device key
    (issue #381) -- so the config flow mints the current version directly
    rather than walking through them.

    Pinned rather than compared to a constant on purpose: the two must be
    bumped together, and a migration step added without moving the flow's
    VERSION never runs at all, because Home Assistant only calls
    async_migrate_entry for an entry *behind* the flow's version.
    """
    from custom_components.localthings.config_flow import LocalThingsConfigFlow

    assert LocalThingsConfigFlow.VERSION == 5
