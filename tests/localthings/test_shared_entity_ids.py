"""Alternative descriptors that share one entity id (issue #879).

The microwave's `lamp_light` is two LightDescs on /mode/vs/0, one for a bare
`Lamp` token with brightness and one for `UpperLamp`, gated by exists_fn. The
platforms register one entity per id, which must be built from the descriptor
the appliance's own options include.
"""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.light import ColorMode
from homeassistant.core import HomeAssistant

from custom_components.localthings.registration import binding, bound_id, by_id
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

from .test_offline_setup import _reachable

_COORD = "custom_components.localthings.coordinator.LocalThingsCoordinator"


def _lamp_lights(resources):
    registry = resolve(resources, device_types=("oic.wk.d", "oic.d.microwave"))
    assert registry is not None
    bound = discover(resources, registry.capabilities, registry.pattern_capabilities)
    return [b for b in bound if b.desc.key == "lamp_light"]


def test_the_included_alternative_wins_whichever_comes_first():
    resources = _load_device("microwave_me7500d")
    lamp, upper = _lamp_lights(resources)
    assert bound_id(lamp) == bound_id(upper)
    included = {id(lamp)}

    for order in ([lamp, upper], [upper, lamp]):
        chosen = by_id(order, lambda b: id(b) in included)
        assert list(chosen.values()) == [lamp]


async def test_a_bare_lamp_microwave_gets_the_brightness_light(
    hass: HomeAssistant, mock_entry
) -> None:
    """Built from the UpperLamp descriptor, the ME7500D's lamp was on/off
    only, and its writes named a token the board doesn't have."""
    writes: list[tuple[str, dict]] = []

    async def _put(self, path_segs, body, href, **kwargs) -> bool:
        writes.append((href, body))
        return True

    with _reachable(_load_device("microwave_me7500d")), patch(f"{_COORD}._async_put", _put):
        await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        (light,) = hass.states.async_all("light")
        assert light.attributes["supported_color_modes"] == [ColorMode.BRIGHTNESS]

        await hass.services.async_call(
            "light", "turn_on", {"entity_id": light.entity_id, "brightness": 255}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert writes == [("/mode/vs/0", {"x.com.samsung.da.options": ["Lamp_High"]})]


def test_alternatives_bind_differently_and_a_rediscovery_does_not():
    resources = _load_device("microwave_me7500d")
    lamp, upper = _lamp_lights(resources)
    (again, _) = _lamp_lights(resources)

    assert binding(lamp) != binding(upper)
    assert binding(again) == binding(lamp)


async def test_an_entity_built_from_one_alternative_is_rebuilt_from_the_other(
    hass: HomeAssistant,
) -> None:
    """A first read that includes neither keeps the first; once a later read
    includes the other, the entity is rebuilt from it."""
    from typing import Any, cast

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.localthings.const import DOMAIN
    from custom_components.localthings.entity import async_add_bound_entities

    from .test_add_bound_entities import _Coordinator, _Fake

    lamp, upper = _lamp_lights(_load_device("microwave_me7500d"))
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = _Coordinator(hass, [lamp, upper])
    included: set[int] = set()
    added: list[_Fake] = []
    with patch(
        "custom_components.localthings.entity.is_included",
        lambda b, c, live=False: id(b) in included,
    ):
        async_add_bound_entities(
            entry,
            cast(Any, coordinator),
            cast(Any, lambda new: added.extend(new)),
            lambda b: True,
            cast(Any, _Fake),
        )
        assert [e._bound for e in added] == [lamp]

        included.add(id(upper))
        coordinator.registration.judge(coordinator.bound, [], lambda b: True)
        coordinator.update()
        await hass.async_block_till_done(wait_background_tasks=True)

    assert added[0].removed
    assert added[-1]._bound is upper


async def test_a_later_read_grows_the_alternative_it_includes(
    hass: HomeAssistant, mock_entry
) -> None:
    """The first read misses /mode/vs/0; the next reports an UpperLamp board.
    Growth judged only the first alternative per id, which this board
    excludes, so its lamp light was never added."""
    from custom_components.localthings.coordinator import LocalThingsCoordinator

    full = _load_device("microwave_me7500d")
    options = full["/mode/vs/0"]["x.com.samsung.da.options"]
    full["/mode/vs/0"]["x.com.samsung.da.options"] = [
        "UpperLamp_Off" if o == "Lamp_Off" else o for o in options
    ]
    short = {h: r for h, r in full.items() if h != "/mode/vs/0"}
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._run_discovery(short)
    coordinator._judge_registration(short)
    assert not [b for b in coordinator.bound if b.desc.key == "lamp_light"]

    assert await coordinator._grow(full, [], bank=full)

    (light,) = [b for b in coordinator.bound if b.desc.key == "lamp_light"]
    assert light.desc.exists_fn is not None and light.desc.exists_fn(full["/mode/vs/0"], full)
    assert coordinator.registrable(light)
