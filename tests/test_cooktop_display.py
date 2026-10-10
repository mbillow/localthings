"""Cooktop display options preserve entity-registry choices across flow sessions."""

from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings import cooktop_display
from custom_components.localthings.const import (
    CONF_BYPASS_REMOTE_CONTROL,
    CONF_DEVICE_KEY,
    CONF_HOST,
    CONF_LEARN_MODES,
    DOMAIN,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.entity import LocalThingsEntity
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import BinarySensorDesc
from tests.conftest import _load_device

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_FIXTURE = "cooktop_tp6x_ct_16k_8888"
_MANAGED = "compact_cooktop_disabled"


@pytest.fixture
def hass_config_dir() -> str:
    return str(Path(__file__).resolve().parents[1])


def _entry(hass: HomeAssistant, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.1", CONF_DEVICE_KEY: "COMPACT-TEST"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry, fixture: str = _FIXTURE):
    resources = _load_device(fixture)
    registry = resolve(resources)
    assert registry is not None
    coordinator = LocalThingsCoordinator(hass, entry)
    coordinator.bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return coordinator


def _register(hass, entry, coordinator, key, disabled_by=None):
    bound = next(bound for bound in coordinator.bound if bound.desc.key == key)
    entity = LocalThingsEntity(coordinator, bound)
    unique_id = entity.unique_id
    assert unique_id is not None
    return er.async_get(hass).async_get_or_create(
        "binary_sensor" if isinstance(bound.desc, BinarySensorDesc) else "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        disabled_by=disabled_by,
    )


def _disabled(hass, row):
    current = er.async_get(hass).async_get(row.entity_id)
    assert current is not None
    return current.disabled_by


async def _display_form(hass: HomeAssistant, entry: MockConfigEntry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "cooktop_display"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "cooktop_display"
    return result


async def _set_compact(hass: HomeAssistant, entry: MockConfigEntry, enabled: bool):
    form = await _display_form(hass, entry)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], user_input={cooktop_display.COMPACT: enabled}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[cooktop_display.COMPACT] is enabled


async def test_compact_round_trip_preserves_user_disabled_and_unrelated_rows(hass: HomeAssistant):
    entry = _entry(hass, options={CONF_LEARN_MODES: False})
    coordinator = _coordinator(hass, entry)
    registry = er.async_get(hass)
    details = [
        _register(hass, entry, coordinator, key)
        for key in ("burner_0_power_level", "burner_3_pan_size", "main_timer_set")
    ]
    user_disabled = _register(
        hass, entry, coordinator, "burner_0_hot_surface", er.RegistryEntryDisabler.USER
    )
    previously_disabled = _register(
        hass, entry, coordinator, "main_timer_current", er.RegistryEntryDisabler.INTEGRATION
    )
    summaries = [
        _register(hass, entry, coordinator, key)
        for key in ("burner_0_state", "main_timer_state", "power_state")
    ]
    unrelated = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{coordinator.device_key}_burner_99_power_level",
        config_entry=entry,
    )
    # A matching unique ID under another entry must not be claimed by this option.
    other_entry = _entry(hass)
    other_row = _register(hass, other_entry, coordinator, "burner_5_power_level")

    form = await _display_form(hass, entry)
    schema = form["data_schema"]
    assert schema is not None
    assert schema({})[cooktop_display.COMPACT] is False
    await _set_compact(hass, entry, True)

    assert all(_disabled(hass, row) is er.RegistryEntryDisabler.INTEGRATION for row in details)
    assert _disabled(hass, user_disabled) is er.RegistryEntryDisabler.USER
    assert _disabled(hass, previously_disabled) is er.RegistryEntryDisabler.INTEGRATION
    assert all(_disabled(hass, row) is None for row in [*summaries, unrelated, other_row])
    assert set(entry.options[_MANAGED]) == {row.unique_id for row in details}
    assert entry.options[CONF_LEARN_MODES] is False

    details[0] = registry.async_update_entity(
        details[0].entity_id, new_entity_id="sensor.my_burner"
    )
    _coordinator(hass, entry)
    form = await _display_form(hass, entry)
    schema = form["data_schema"]
    assert schema is not None
    assert schema({})[cooktop_display.COMPACT] is True
    await _set_compact(hass, entry, False)

    assert all(_disabled(hass, row) is None for row in [*details, *summaries, unrelated, other_row])
    assert _disabled(hass, user_disabled) is er.RegistryEntryDisabler.USER
    assert _disabled(hass, previously_disabled) is er.RegistryEntryDisabler.INTEGRATION
    assert entry.options[_MANAGED] == []
    assert entry.options[CONF_LEARN_MODES] is False


async def test_repeated_compact_setting_and_restore_preserve_later_user_overrides(
    hass: HomeAssistant,
):
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    registry = er.async_get(hass)
    user_disabled = _register(hass, entry, coordinator, "burner_0_power_level")
    user_enabled = _register(hass, entry, coordinator, "burner_1_power_level")
    await _set_compact(hass, entry, True)
    managed = entry.options[_MANAGED]

    registry.async_update_entity(user_disabled.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    registry.async_update_entity(user_enabled.entity_id, disabled_by=None)
    await _set_compact(hass, entry, True)

    assert _disabled(hass, user_disabled) is er.RegistryEntryDisabler.USER
    assert _disabled(hass, user_enabled) is None
    assert entry.options[_MANAGED] == managed

    await _set_compact(hass, entry, False)
    await _set_compact(hass, entry, False)

    assert _disabled(hass, user_disabled) is er.RegistryEntryDisabler.USER
    assert _disabled(hass, user_enabled) is None
    assert entry.options[_MANAGED] == []


async def test_normal_settings_preserve_compact_metadata_and_later_restoration(hass: HomeAssistant):
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    detail = _register(hass, entry, coordinator, "burner_0_power_level")
    await _set_compact(hass, entry, True)
    managed = entry.options[_MANAGED]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "settings"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_BYPASS_REMOTE_CONTROL: True}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True
    assert entry.options[cooktop_display.COMPACT] is True
    assert entry.options[_MANAGED] == managed
    assert _disabled(hass, detail) is er.RegistryEntryDisabler.INTEGRATION

    await _set_compact(hass, entry, False)

    assert _disabled(hass, detail) is None
    assert entry.options[CONF_BYPASS_REMOTE_CONTROL] is True


@pytest.mark.parametrize(
    ("fixture", "offered"),
    [(_FIXTURE, True), ("induction_cooktop", False), ("refrigerator", False), (None, False)],
)
async def test_compact_menu_is_offered_only_for_summary_capable_cooktops(
    hass: HomeAssistant, fixture, offered
):
    entry = _entry(hass)
    if fixture is not None:
        _coordinator(hass, entry, fixture)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    menu = result["menu_options"]
    assert menu is not None
    assert ("cooktop_display" in menu) is offered
