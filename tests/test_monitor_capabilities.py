"""M7 Smart Monitors (#815): Tizen TV boards with the /sec/tv/* surface."""

from typing import Any

import pytest

from custom_components.localthings.registry import flat
from custom_components.localthings.registry.adapter import flatten
from custom_components.localthings.registry.by_type import resolve
from custom_components.localthings.registry.capabilities import tv
from custom_components.localthings.registry.discovery import discover
from custom_components.localthings.registry.entities import SelectDesc
from custom_components.localthings.registry.redact import REDACTED, redact_resources
from tests.conftest import _load_device

DEVICE_TYPES = ("oic.wk.d", "x.com.st.d.monitor")
FIXTURES = ("monitor_ls32dm702", "monitor_ls43dm703")


def _bound(name):
    resources = _load_device(name)
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.name == "monitor"
    unbound = []
    bound = discover(resources, reg.capabilities, reg.pattern_capabilities, log=unbound.append)
    return bound, resources, unbound


@pytest.mark.parametrize("name", FIXTURES)
def test_no_unbound_hrefs(name):
    _, _, unbound = _bound(name)
    assert unbound == []


def test_routes_on_oic_d_alone():
    resources = _load_device(FIXTURES[0])
    assert resolve(resources) is None
    reg = resolve(resources, device_types=DEVICE_TYPES)
    assert reg is not None and reg.subscribe_tiers == ()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "monitor_ls32dm702",
            {"network_connection": "wifi", "illuminance": 602, "picture_mode": "modeGen_458775"},
        ),
        (
            "monitor_ls43dm703",
            {"network_connection": "wifi", "illuminance": 118, "picture_mode": "modeEco"},
        ),
    ],
)
def test_state(name, expected):
    bound, resources, _ = _bound(name)
    assert flatten(bound, resources) == expected


def test_picture_mode_writes_only_advertised_ids():
    rep = _load_device("monitor_ls32dm702")[tv.HREF_PICTURE]
    desc = tv.PICTURE_MODE.entities[0]
    assert isinstance(desc, SelectDesc) and desc.write_fn is not None
    assert desc.control_response
    # A monitor idle at startup still gets the select once its modes appear.
    assert desc.dynamic_presence
    write: Any = desc.write_fn
    assert write("modeStandardPC", rep, tv.HREF_PICTURE) == (
        ["sec", "tv", "mode", "picture"],
        {tv.CURRENT_MODE: "modeStandardPC"},
    )
    # A label is not an id, and an unadvertised id is not sent.
    assert write("그래픽 화면", rep, tv.HREF_PICTURE) is None
    assert write("modeDynamic", rep, tv.HREF_PICTURE) is None
    read_only = {**rep, "if": ["oic.if.baseline"]}
    assert write("modeStandardPC", read_only, tv.HREF_PICTURE) is None


def test_duplicate_input_names_are_told_apart_by_id():
    # LS32DM702: HDMI1 and USB-C are both named "PC".
    rep = _load_device("monitor_ls32dm702")[tv.HREF_INPUT]
    assert tv.mode_options(rep) == {"HDMI1": "PC (HDMI1)", "USB-C": "PC (USB-C)"}
    # LS43DM703 on the tuner: one empty row.
    assert tv.mode_options(_load_device("monitor_ls43dm703")[tv.HREF_INPUT]) == {}


def test_appdata_mailboxes_are_never_read_and_redacted_whole():
    links = [{"href": "/sec/tv/appdata/ssoservice"}, {"href": tv.HREF_PICTURE}]
    assert flat.flat_hrefs(links) == [tv.HREF_PICTURE]
    rep = {"x.com.samsung.tv.tvtomobile": '{"data": "{\\"account_id\\": \\"someone\\"}"}'}
    assert redact_resources({"/sec/tv/appdata/ssoservice": rep}) == {
        "/sec/tv/appdata/ssoservice": {"x.com.samsung.tv.tvtomobile": REDACTED}
    }
