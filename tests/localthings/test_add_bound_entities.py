"""entity.async_add_bound_entities: in-place adds and rebinding (issue #807)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.const import DOMAIN
from custom_components.localthings.entity import async_add_bound_entities
from custom_components.localthings.registration import Registration, bound_id
from tests.conftest import _discover_full, _load_device_full


class _Coordinator:
    """Just what async_add_bound_entities reads."""

    def __init__(self, hass: HomeAssistant, bound) -> None:
        self.hass = hass
        self.bound = list(bound)
        self.registration = Registration()
        self.registration.judge(self.bound, [], lambda b: True)
        self._listeners = []

    def registrable(self, bound) -> bool:
        return bound_id(bound) in self.registration.static

    def async_add_listener(self, update):
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    def update(self) -> None:
        for listener in list(self._listeners):
            listener()


class _Fake:
    """An entity as the helper sees it: its bound, and async_remove."""

    def __init__(self, bound) -> None:
        self._bound = bound
        self.removed = False

    async def async_remove(self) -> None:
        self.removed = True


async def test_an_entity_bound_differently_is_replaced(hass: HomeAssistant) -> None:
    """A later discovery binding the same id to another href replaces the
    entity, as the reload this design dropped used to rebuild it."""
    main, oic_res, seeds = _load_device_full("airconditioner_fac_bora_2in1")
    bound, *_ = _discover_full(main, oic_res, seeds)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = _Coordinator(hass, bound)
    added: list[_Fake] = []
    async_add_bound_entities(
        entry,
        cast(Any, coordinator),
        cast(Any, lambda new: added.extend(new)),
        lambda b: True,
        cast(Any, _Fake),
    )
    assert len(added) == len(bound)
    old = added[0]

    moved = replace(old._bound, href=old._bound.href + "/moved")
    coordinator.bound = [moved if b is old._bound else b for b in coordinator.bound]
    coordinator.registration.judge(coordinator.bound, [], lambda b: True)
    coordinator.update()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert old.removed
    assert added[-1]._bound == moved
    assert len(added) == len(bound) + 1


async def test_an_unchanged_rediscovery_replaces_nothing(hass: HomeAssistant) -> None:
    main, oic_res, seeds = _load_device_full("airconditioner_fac_bora_2in1")
    bound, *_ = _discover_full(main, oic_res, seeds)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = _Coordinator(hass, bound)
    added: list[_Fake] = []
    async_add_bound_entities(
        entry,
        cast(Any, coordinator),
        cast(Any, lambda new: added.extend(new)),
        lambda b: True,
        cast(Any, _Fake),
    )
    # A live discovery produces new, equal BoundEntity objects.
    coordinator.bound = [replace(b) for b in coordinator.bound]
    coordinator.registration.judge(coordinator.bound, [], lambda b: True)
    coordinator.update()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(added) == len(bound)
    assert not any(e.removed for e in added)
