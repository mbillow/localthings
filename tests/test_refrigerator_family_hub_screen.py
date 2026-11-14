"""Issue #823: a Family Hub's screen resources are known, not coverage gaps."""

from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.discovery import discover
from tests.conftest import _load_device

# Trimmed from the 25K_REF_LCD_FHUB10.0 diagnostics in #823.
_SCREEN = {
    "/accessibility/vs/0": {
        "rt": ["x.com.samsung.da.accessibility"],
        "if": ["oic.if.a"],
        "voiceguide": {"value": "off", "supportedValues": ["on", "off"]},
        "grayscale": {"value": "off", "supportedValues": ["on", "off"]},
        "fontsize": {"value": 2, "supportedValues": [0, 1, 2, 3]},
    },
    "/udo/contents/provider/vs/0": {
        "rt": ["x.com.samsung.udo.contents.provider"],
        "if": ["oic.if.a"],
        "x.com.samsung.da.updatetime": "2026-11-11T09:47:26Z",
        "x.com.samsung.da.contents": [
            {
                "x.com.samsung.da.fileid": "00000000-0000-4000-8000-000000000000",
                "x.com.samsung.da.mimetype": "image/jpeg",
                "x.com.samsung.da.expiredtime": "2026-11-11T08:26:35Z",
            }
        ],
    },
}


def test_family_hub_screen_resources_bind_without_entities():
    resources = {**_load_device("refrigerator"), **_SCREEN}
    reg = resolve(resources)
    assert reg is not None and reg.name == "refrigerator"
    unbound: list[str] = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    assert not set(_SCREEN) & set(unbound)
    assert not any(b.href in _SCREEN for b in bound)
