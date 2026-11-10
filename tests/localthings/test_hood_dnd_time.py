"""DND service writes keep both UTC clock halves on one local offset."""

import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.coordinator import LocalThingsCoordinator
from tests.conftest import _load_device
from .test_offline_setup import _reachable

HREF = "/airlevelcheck/vs/0"
FIELD = "x.com.samsung.da.periodicSensingSkipTime"


@pytest.mark.parametrize(
    "date,zone,want",
    [
        ("2026-11-01T03:00:00+00:00", "America/Los_Angeles", "05001200"),
        ("2026-03-08T04:00:00+00:00", "America/Los_Angeles", "06001300"),
        ("2026-11-01T20:00:00+00:00", "UTC", "22000500"),
        ("2026-11-01T20:00:00+00:00", "Asia/Kolkata", "16302330"),
        ("2026-11-01T20:00:00+00:00", "America/Phoenix", "05001200"),
    ],
)
@pytest.mark.parametrize("order", [("start", "end"), ("end", "start")])
@pytest.mark.parametrize("raw", ["00000000", "05001200", None])
async def test_dnd_services_keep_seven_hour_window(
    hass, mock_entry, monkeypatch, date, zone, want, order, raw
):
    now = datetime.datetime.fromisoformat(date).astimezone(ZoneInfo(zone))
    resources = _load_device("range_hood")
    # Keep the entities present even when a firmware value is null.
    resources[HREF][FIELD] = raw if raw is not None else ""
    sent = []

    async def send(self, path, body, *args, **kwargs):
        assert path == ["airlevelcheck", "vs", "0"]
        assert set(body) == {FIELD}
        sent.append(body[FIELD])
        resources[HREF].update(body)
        return True

    with (
        _reachable(resources),
        patch.object(LocalThingsCoordinator, "_CONFIRM_DELAY_S", 0),
        patch.object(LocalThingsCoordinator, "_async_put", send),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        coordinator = hass.data[DOMAIN][mock_entry.entry_id]
        with (
            patch.object(dt_util, "now", return_value=now),
            patch.object(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo(zone)),
        ):
            for part in order:
                bound = next(
                    b for b in coordinator.bound if b.desc.key == f"hood_do_not_disturb_{part}"
                )
                entity_id = registry.async_get_entity_id(
                    "time", DOMAIN, f"{DOMAIN}_{coordinator.device_key}_{bound.desc.key}"
                )
                assert entity_id is not None
                await hass.services.async_call(
                    "time",
                    "set_value",
                    {"entity_id": entity_id, "time": "22:00:00" if part == "start" else "05:00:00"},
                    blocking=True,
                )
            assert sent[-1] == want
            assert coordinator.entity_rep(HREF)[FIELD] == want
