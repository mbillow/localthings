"""Polling a device with no /device/0 Collection (registry/flat.py).

Covers the 4.04 detection, the per-href reads, and the setup probe's
matching fallback.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, cast

import pytest
from homeassistant.core import HomeAssistant

from custom_components.localthings.config_flow import UnexpectedResponse, _read_device
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.observe import MODE_POLL
from custom_components.localthings.registry.flat import flat_hrefs, iter_resources
from custom_components.localthings.registry.identity import DeviceIdentity

from .conftest import FakeObserveSession

_LINKS = [
    {"href": "/oic/d", "rt": ["oic.wk.d"]},
    {"href": "/oic/p", "rt": ["oic.wk.p"]},
    {"href": "/oic/res", "rt": ["oic.wk.res"]},
    {"href": "/oic/sec/doxm", "rt": ["oic.r.doxm"]},
    {"href": "/oic/sec/pstat", "rt": ["oic.r.pstat"]},
    {"href": "/device/0", "rt": ["oic.wk.col"]},
    {"href": "/sec/switch/binary", "rt": ["oic.r.switch.binary"]},
    {"href": "/sec/audio", "rt": ["oic.r.audio"]},
    {"href": "/capability/mediaPlayback/main/0", "rt": ["x.com.st.mediaplayer"]},
    # Duplicate links across interfaces, and junk a reader must survive.
    {"href": "/sec/audio", "rt": ["oic.r.audio"], "if": ["oic.if.a"]},
    {"rt": ["oic.wk.col"]},
    "not-a-link",
]

_GETTABLE = ["/capability/mediaPlayback/main/0", "/sec/audio", "/sec/switch/binary"]

_RESOURCES = {
    "/sec/switch/binary": {"value": True},
    "/sec/audio": {"volume": 11, "mute": False},
    "/capability/mediaPlayback/main/0": {"modes": ["stop"]},
}


def _identity(links=_LINKS, device_types=("oic.wk.d", "oic.d.networkaudio")) -> DeviceIdentity:
    return DeviceIdentity(
        manufacturer="Samsung Electronics",
        model="HW-TEST",
        name="Test AV",
        serial=None,
        device_id="7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31",
        device_types=device_types,
        raw={"/oic/res": links},
    )


# As the real AV boards answer: /oic/res advertises no /device/0.
_AV_LINKS = [
    link for link in _LINKS if not (isinstance(link, dict) and link.get("href") == "/device/0")
]


class FakeFlatSession:
    """A Transport stand-in whose /oic/res advertises `links` and whose
    property resources come from `resources`; /device/0 answers 4.04."""

    supports_observe = True

    _OIC_P: ClassVar[dict] = {"mnmn": "Samsung Electronics", "mnmo": "HW-TEST"}
    _OIC_D: ClassVar[dict] = {
        "rt": ["oic.wk.d", "oic.d.networkaudio"],
        "di": "7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31",
        "n": "Test AV",
    }

    def __init__(self, resources=None, links=_LINKS, device0_code=0x84):
        self.resources = resources if resources is not None else _RESOURCES
        self.links = links
        self.device0_code = device0_code
        self.reads: list[str] = []

    def read(self, path, timeout=None):
        href = "/" + "/".join(path)
        self.reads.append(href)
        if list(path) == ["device", "0"]:
            return self.device0_code, None
        if href == "/oic/res":
            return 0x45, self.links
        if href == "/oic/p":
            return 0x45, self._OIC_P
        if href == "/oic/d":
            return 0x45, self._OIC_D
        rep = self.resources.get(href)
        return (0x45, rep) if rep is not None else (0x84, None)

    def pace(self):
        pass

    def diagnostics(self):
        return {}


class _BatchBoard(FakeFlatSession):
    _OIC_D: ClassVar[dict] = {**FakeFlatSession._OIC_D, "rt": ["oic.wk.d", "oic.d.washer"]}


def test_flat_hrefs_keeps_only_pollable_resources() -> None:
    assert flat_hrefs(_LINKS) == _GETTABLE


def test_flat_hrefs_unwraps_a_di_grouped_answer() -> None:
    """The HW-S61B itself answers /oic/res as one {'di', 'links'} entry
    rather than a bare link array."""
    wrapped = [{"di": "7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31", "links": _LINKS}]
    assert flat_hrefs(wrapped) == _GETTABLE


def test_iter_resources_tolerates_gaps() -> None:
    sess = FakeFlatSession(resources={"/sec/audio": {"volume": 3}})
    out = dict(
        iter_resources(
            sess, _GETTABLE, timeout=5.0, budget=20.0, logger=logging.getLogger(__name__)
        )
    )
    assert out == {"/sec/audio": {"volume": 3}}
    assert sess.reads == _GETTABLE


def test_iter_resources_budget_spent_returns_what_it_has() -> None:
    sess = FakeFlatSession()
    out = dict(
        iter_resources(sess, _GETTABLE, timeout=5.0, budget=0.0, logger=logging.getLogger(__name__))
    )
    assert out == {}
    assert sess.reads == []


def test_poll_once_switches_to_flat_on_404(hass: HomeAssistant, mock_entry) -> None:
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    sess = FakeFlatSession()
    coordinator._session = cast(Any, sess)
    # As the real AV boards answer: /oic/res advertises no /device/0.
    coordinator._identity = _identity(_AV_LINKS)

    resources = coordinator._poll_once()

    assert coordinator._flat is True
    assert resources == _RESOURCES
    # The batch href was tried exactly once; every later poll goes straight
    # to the per-href reads.
    coordinator._poll_once()
    assert sess.reads.count("/device/0") == 1
    assert "/oic/sec/doxm" not in sess.reads


def test_a_board_advertising_device0_does_not_go_flat_on_404(
    hass: HomeAssistant, mock_entry
) -> None:
    """A batch board answering 4.04 while it boots is a failed poll."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._session = cast(Any, FakeFlatSession())
    coordinator._identity = _identity(device_types=("oic.wk.d", "oic.d.washer"))

    with pytest.raises(RuntimeError):
        coordinator._poll_once()
    assert coordinator._flat is False


def test_flat_poll_after_discovery_cold_sweep_every_n_cycles(
    hass: HomeAssistant, mock_entry
) -> None:
    """Off-cadence cycles cost one liveness GET; every Nth reads bound and
    cold-tier hrefs, minus hot/warm ones."""
    from types import SimpleNamespace

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = True
    coordinator._discovered = True
    coordinator._hot_hrefs = ["/sec/audio"]
    coordinator._warm_hrefs = ["/sec/switch/binary"]
    coordinator.bound = cast(
        Any,
        [
            SimpleNamespace(href="/sec/audio"),
            SimpleNamespace(href="/sec/switch/binary"),
            SimpleNamespace(href="/capability/mediaPlayback/main/0"),
        ],
    )
    # A cold-tier capability with no entity: no BoundEntity, still swept.
    coordinator._cold_hrefs = ["/sec/networkaudio/info"]
    resources_map = {
        "/capability/mediaPlayback/main/0": {"modes": ["stop"]},
        "/sec/networkaudio/info": {"x.info": "value"},
    }
    sess = FakeFlatSession(resources=resources_map)
    coordinator._session = cast(Any, sess)

    for _ in range(LocalThingsCoordinator._FLAT_FULL_EVERY_N_POLLS - 1):
        assert coordinator._poll_once() == {}
    assert sess.reads == ["/oic/d"] * (LocalThingsCoordinator._FLAT_FULL_EVERY_N_POLLS - 1)

    resources = coordinator._poll_once()
    assert resources == resources_map
    assert sess.reads[-2:] == ["/capability/mediaPlayback/main/0", "/sec/networkaudio/info"]


def test_late_404_keeps_a_discovered_batch_device(hass: HomeAssistant, mock_entry) -> None:
    """A 4.04 on an already-discovered batch board is a failed poll, not flat."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._discovered = True
    sess = FakeFlatSession()
    coordinator._session = cast(Any, sess)
    coordinator._identity = _identity()

    with pytest.raises(RuntimeError):
        coordinator._poll_once()
    assert coordinator._flat is False


def test_a_board_dozing_in_standby_is_not_discovered(hass: HomeAssistant, mock_entry) -> None:
    """A handshake that completes, then most reads time out: discovering from
    that would register a few entities and drop the rest."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._identity = _identity(_AV_LINKS)
    # One of the three advertised resources answers.
    coordinator._session = cast(Any, FakeFlatSession(resources={"/sec/audio": {"volume": 3}}))

    with pytest.raises(RuntimeError, match="no resource answered"):
        coordinator._poll_once()
    assert coordinator._flat is True


def test_flat_poll_with_every_href_failing_raises(hass: HomeAssistant, mock_entry) -> None:
    """No href answering on a just-connected device means a dead session."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = True
    coordinator._identity = _identity()
    coordinator._session = cast(Any, FakeFlatSession(resources={}))

    with pytest.raises(RuntimeError, match="no resource answered"):
        coordinator._poll_once()


async def test_flat_device_attempts_observe_like_any_other(hass: HomeAssistant, mock_entry) -> None:
    """A flat device on the default subscribe scope still attempts OBSERVE."""
    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = True
    coordinator._discovered = True
    coordinator._hot_hrefs = ["/sec/audio"]
    coordinator._session = cast(Any, FakeObserveSession())

    await coordinator._attempt_observe_mode()

    assert cast(FakeObserveSession, coordinator._session).subscribed == ["/sec/audio"]
    assert coordinator._observe.mode == MODE_POLL


def test_probe_reads_a_flat_device() -> None:
    """The setup probe also reads /oic/res's hrefs when /device/0 answers 4.04."""
    sess = FakeFlatSession(links=_AV_LINKS)

    info = _read_device(sess, "10.0.0.99", 49154)

    assert info["device_key"] == "7b1f0c9e-2a44-4d6b-9f10-4c8e2b5a0d31"
    assert info["model"] == "HW-TEST"
    assert info["manufacturer"] == "Samsung Electronics"
    # /oic/d's rt is the only route on this family.
    assert info["device_type_recognized"] is True
    assert info["device_type_name"] == "soundbar"


def test_probe_of_a_batch_board_answering_4_04_is_unusable() -> None:
    """A batch board answering 4.04 (still booting) is not read href by
    href; setup reports it instead."""
    sess = _BatchBoard()
    with pytest.raises(UnexpectedResponse):
        _read_device(sess, "10.0.0.99", 49154)
    assert sess.reads[-1] == "/device/0"


def test_probe_flat_device_answering_nothing_is_unusable() -> None:
    sess = FakeFlatSession(resources={}, links=_AV_LINKS)
    with pytest.raises(UnexpectedResponse, match=r"4\.04"):
        _read_device(sess, "10.0.0.99", 49154)


def test_flat_liveness_without_an_answer_fails_the_cycle(hass: HomeAssistant, mock_entry) -> None:
    """A soundbar switched off by its own remote: the off-cadence liveness GET gets
    nothing, and the cycle fails at once instead of being taken for a slow
    transfer."""

    class _Silent(FakeFlatSession):
        def read(self, path, timeout=None):
            raise TimeoutError("no answer")

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = True
    coordinator._discovered = True
    coordinator._session = cast(Any, _Silent())

    with pytest.raises(RuntimeError, match="liveness"):
        coordinator._poll_once()


def test_flat_reads_keep_no_wifi_secrets_or_onboarding_tokens() -> None:
    links = [{"href": h} for h in ("/WiFiConfResURI", "/CoapCloudConfResURI", "/sec/audio")]
    assert flat_hrefs(links) == ["/WiFiConfResURI", "/sec/audio"]
    sess = FakeFlatSession(
        resources={"/WiFiConfResURI": {"tnn": "HomeNet", "cd": "secret", "wat": "WPA2"}}
    )
    reps = dict(iter_resources(sess, ["/WiFiConfResURI"], timeout=1, logger=logging.getLogger()))
    assert reps["/WiFiConfResURI"] == {"tnn": "wifi", "wat": "WPA2"}


def test_flat_reads_keep_no_network_identity() -> None:
    """The BSSID, and the SSIDs nested in a soundbar's networkInfo, are
    dropped too; what the entities read stays."""
    info = {"ssid": "HomeNet", "bssid": "x", "wifidirectssid": "y", "rssi": 3, "ch": 8}
    sess = FakeFlatSession(
        resources={
            "/WiFiConfResURI": {"tnn": "ethernet", "x.com.samsung.bssid": "x"},
            "/sec/networkaudio/networkInfo": {"x.com.samsung.networkaudio.networkInfo": info},
        }
    )
    reps = dict(
        iter_resources(
            sess,
            ["/WiFiConfResURI", "/sec/networkaudio/networkInfo"],
            timeout=1,
            logger=logging.getLogger(),
        )
    )
    assert reps == {
        "/WiFiConfResURI": {"tnn": "ethernet"},
        "/sec/networkaudio/networkInfo": {
            "x.com.samsung.networkaudio.networkInfo": {"rssi": 3, "ch": 8}
        },
    }


def test_iter_resources_survives_a_failing_pace() -> None:
    """A pace that raises (a session closed underneath) costs that href only."""

    class _BadPace(FakeFlatSession):
        def pace(self):
            raise RuntimeError("no session")

    sess = _BadPace()
    reps = dict(iter_resources(sess, _GETTABLE, timeout=1, logger=logging.getLogger()))
    assert list(reps) == _GETTABLE[:1]


async def test_an_off_flat_board_stays_unavailable(hass: HomeAssistant, mock_entry) -> None:
    """Every cycle whose handshake times out fails: none is deferred as a slow
    transfer, which would show the last state as live in between."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = coordinator._discovered = True
    coordinator._observe.apply("/sec/audio", {"volume": 3}, source="poll")

    def _off():
        coordinator._handshake_failed = True
        raise TimeoutError("handshake timed out")

    with (
        patch.object(coordinator, "_poll_once", side_effect=_off),
        patch.object(coordinator, "_close_session"),
        patch("custom_components.localthings.coordinator.asyncio.sleep", new_callable=AsyncMock),
    ):
        for _ in range(LocalThingsCoordinator._POLL_TIMEOUT_LIMIT + 1):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()


def test_a_board_back_from_off_is_read_in_full_at_once(hass: HomeAssistant, mock_entry) -> None:
    """The poll that finds it answering again reads everything, not one
    liveness GET: its settings may have changed while it was off."""
    from types import SimpleNamespace

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = coordinator._discovered = True
    coordinator.bound = cast(Any, [SimpleNamespace(href="/sec/audio")])
    sess = FakeFlatSession()
    coordinator._session = cast(Any, sess)
    coordinator._failed_cycles = 2

    # In the order _async_update_data runs them.
    coordinator._poll_once()
    coordinator._mark_device_answered()
    assert sess.reads == ["/sec/audio"]

    # Back on the usual cadence afterwards.
    coordinator._poll_once()
    assert sess.reads[-1] == "/oic/d"


async def test_flat_subpolls_rotate_so_no_href_is_starved(hass: HomeAssistant, mock_entry) -> None:
    """The budget covers the paced reads plus about two timeouts: hrefs that
    keep timing out at the head of the list mustn't keep the others from ever
    being read."""
    from unittest.mock import AsyncMock, patch

    coordinator = LocalThingsCoordinator(hass, mock_entry)
    coordinator._flat = True
    coordinator._hot_hrefs = ["/a", "/b", "/c", "/d"]
    coordinator._warm_hrefs = []
    first: list[str] = []

    budgets: list[float] = []

    def _capture(hrefs, *args):
        first.append(hrefs[0])
        budgets.append(args[2])

    with (
        patch.object(coordinator, "_poll_hrefs_blocking", side_effect=_capture),
        patch("custom_components.localthings.coordinator.asyncio.sleep", new_callable=AsyncMock),
    ):
        await coordinator._run_subpolls()

    assert set(first) == {"/a", "/b", "/c", "/d"}
    # Four live reads fit, with room for two timeouts.
    assert set(budgets) == {2 * coordinator._FLAT_TIMEOUT_S + 4 * coordinator._FLAT_READ_S}
