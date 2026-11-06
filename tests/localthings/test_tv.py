"""A synthetic PSK TV session exercises the captured mode resources through HA."""

from dataclasses import replace
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.localthings.const import CONF_MODEL, DOMAIN
from custom_components.localthings.registry.capabilities import tv
from tests.conftest import _load_device

from .test_soundbar import _IDENTITY, ENTRY_DATA, FakeSoundbarSession, _mock_entry


async def test_tv_selects_register_and_write_distinct_inputs(hass: HomeAssistant):
    # Reuse the AV transport contract, not its soundbar capabilities.
    session = FakeSoundbarSession()
    session.resources = _load_device("tv_qn90a")
    identity = replace(
        _IDENTITY,
        model="QE50QN90AATXXU",
        device_types=("oic.wk.d", "oic.d.tv"),
        raw={"/oic/res": [{"href": href} for href in session.resources]},
    )
    entry = _mock_entry(hass)
    hass.config_entries.async_update_entry(entry, data={**ENTRY_DATA, CONF_MODEL: "QE50QN90AATXXU"})
    with (
        patch("custom_components.localthings.coordinator.create_transport", return_value=session),
        patch("custom_components.localthings.coordinator.read_identity", return_value=identity),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.device_type_name == "tv"
    assert coordinator._flat is True
    assert session.subscribed == []
    entities = {
        row.unique_id.rsplit("_tv_", 1)[-1]: row.entity_id
        for row in er.async_get(hass).entities.values()
        if row.config_entry_id == entry.entry_id and row.domain == "select"
    }
    assert set(entities) == {"input", "picture_mode", "sound_mode"}
    state = hass.states.get(entities["input"])
    assert state is not None
    assert state.state == "PC (HDMI1)"
    assert "PC (HDMI4)" in state.attributes["options"]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entities["input"], "option": "PC (HDMI4)"},
        blocking=True,
    )
    assert session.writes == [(["sec", "tv", "inputsource"], {tv.CURRENT_MODE: "HDMI4"})]
    assert session.resources[tv.HREF_INPUT][tv.CURRENT_MODE] == "HDMI4"
    state = hass.states.get(entities["input"])
    assert state is not None and state.state == "PC (HDMI4)"
