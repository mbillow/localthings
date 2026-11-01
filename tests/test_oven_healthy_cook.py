"""Healthy Cook's shared labels preserve the raw codes for each cavity."""

from unittest.mock import AsyncMock

import pytest

from custom_components.localthings.registry.subdevices import MAIN
from custom_components.localthings.select import LocalThingsSelect
from tests.test_legacy_http_lower_cavity import (
    PREFIX,
    _discovered,
    _fake_https,  # noqa: F401 -- fake legacy transport
    _routes,
)


@pytest.mark.parametrize(
    ("divided", "lower", "prefix", "count"),
    [(False, False, "", 6), (True, False, "Upper", 4), (True, True, "Lower", 4)],
)
async def test_healthy_cook_display_and_selection_keep_each_cavitys_raw_codes(
    hass, monkeypatch, divided, lower, prefix, count
):
    coordinator = await _discovered(hass, _routes(divided=divided))
    subdevice = coordinator.subdevices[0] if lower else MAIN
    bound = next(
        b for b in coordinator.bound if b.subdevice == subdevice and b.desc.key == "oven_mode"
    )
    entity = LocalThingsSelect(coordinator, bound)
    send = AsyncMock()
    monkeypatch.setattr(coordinator, "async_send_command", send)

    assert entity.translation_key == "oven_mode"
    for number in range(1, count + 1):
        raw = f"{prefix}Healthycook{number}"
        state = f"{prefix.lower()}_healthycook{number}" if prefix else f"healthycook{number}"
        coordinator._observe.apply(bound.href, {PREFIX + "modes": [raw]}, source="poll")
        coordinator._publish_data()

        assert state in entity.options
        assert entity.current_option == state
        await entity.async_select_option(state)
        send.assert_awaited_with(bound, raw)
