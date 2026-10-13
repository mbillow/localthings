"""Tests for the write_resource/read_resource services (issue #300), the
observe_resource service (issue #607), and the options-flow debug panel
now that it goes through write_resource instead of
calling coordinator.async_raw_write directly (config_flow.py). See
tests/test_coordinator_raw_write.py for the underlying single-write
primitive's own session/validation tests -- these focus on the service
layer: device target resolution, subdevice href translation, ordered
multi-write sequencing, settle/verify_after, and the options-flow rewiring.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, patch

import cbor2
import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.localthings.config_flow import LocalThingsOptionsFlow
from custom_components.localthings.const import (
    CONF_HOST,
    CONF_LEAF_CERT_PEM,
    CONF_LEAF_KEY_PEM,
    CONF_PORT,
    CONF_TRANSPORT,
    DOMAIN,
    SERVICE_OBSERVE_RESOURCE,
    SERVICE_READ_RESOURCE,
    SERVICE_WRITE_RESOURCE,
    TRANSPORT_LEGACY_HTTP,
)
from custom_components.localthings.coordinator import LocalThingsCoordinator
from custom_components.localthings.registry.encode import TYPE_KEY, from_json_safe
from custom_components.localthings.registry.subdevices import MAIN, Subdevice
from custom_components.localthings.services import async_setup_services

ENTRY_DATA = {
    CONF_HOST: "10.0.0.199",
    CONF_PORT: 49154,
    CONF_LEAF_CERT_PEM: "-----BEGIN CERTIFICATE-----\nTEST-LEAF\n-----END CERTIFICATE-----",
    CONF_LEAF_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nTEST-LEAF-KEY\n-----END PRIVATE KEY-----",
}

_SLEEP_TARGET = "custom_components.localthings.coordinator.asyncio.sleep"


class _FakeSession:
    """Stand-in for a Transport: records every write verbatim and answers
    GET from a per-href queue of canned representations, so a test can model
    a value that changes across successive reads of the same href (the
    write's own follow-up GET vs. a later verify_after re-read) -- no real
    DTLS/network involved. Modeled on test_coordinator_raw_write.py's
    _FakeRawWriteSession, extended for multi-step sequences."""

    def __init__(self, post_code: int = 0x44, post_response: object = None):
        self.post_calls: list[tuple[list[str], bytes]] = []
        self.get_calls: list[list[str]] = []
        self.get_queries: list[list[str]] = []
        self._post_code = post_code
        self._post_response = post_response
        self._get_reps: dict[str, list[dict | list]] = {}

    def queue_get(self, href: str, rep: dict | list) -> None:
        """Queue one more canned rep for `href`'s next GET. Once an href's
        queue is down to one entry, that entry keeps answering every
        further GET -- a test only needs to queue the values that
        actually change across calls.

        A list models a Collection's answer (the `[devcol rep, {href, rep},
        ...]` batch), which is not a Property map and so is a shape the
        read path has to carry separately -- see the collection test below.
        """
        self._get_reps.setdefault(href.strip("/"), []).append(rep)

    def write(self, path_segs, body, timeout=None):
        self.post_calls.append((list(path_segs), body))
        # The transport decodes, so the double hands back the decoded body.
        return self._post_code, self._post_response

    def read(self, path_segs, timeout=None, query=()):
        self.get_calls.append(list(path_segs))
        self.get_queries.append(list(query))
        key = "/".join(path_segs)
        queue = self._get_reps.get(key)
        if not queue:
            return 0x45, {}
        rep = queue.pop(0) if len(queue) > 1 else queue[0]
        return 0x45, rep

    def pace(self):
        pass


@pytest.fixture
def coordinator(hass: HomeAssistant) -> LocalThingsCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id="localthings_SERVICES-TEST",
    )
    entry.add_to_hass(hass)
    coord = LocalThingsCoordinator(hass, entry)
    # async_raw_write_sequence kicks a refresh after the write; a real
    # refresh would try to poll a session that doesn't exist for this unit
    # test, so replace it with a no-op the same way
    # test_coordinator_raw_write.py does.
    coord.async_request_refresh = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    async_setup_services(hass)
    return coord


@pytest.fixture
def device_id(hass: HomeAssistant, coordinator: LocalThingsCoordinator) -> str:
    """Register MAIN's HA device under the same identifiers a real config
    entry setup would have used (coordinator.device_info)."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers=coordinator.device_info["identifiers"],
    )
    return device.id


async def _call_write(hass: HomeAssistant, device: str, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_WRITE_RESOURCE,
        data,
        target={"device_id": device},
        blocking=True,
        return_response=True,
    )


async def _call_read_as(hass: HomeAssistant, device: str, context: Context, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_READ_RESOURCE,
        data,
        target={"device_id": device},
        blocking=True,
        return_response=True,
        context=context,
    )


async def _call_read(hass: HomeAssistant, device: str, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_READ_RESOURCE,
        data,
        target={"device_id": device},
        blocking=True,
        return_response=True,
    )


# ----------------------------------------------------------------------
# write_resource: ordered multi-write sequencing
# ----------------------------------------------------------------------


async def test_write_resource_posts_in_order_with_exact_bodies(hass, coordinator, device_id):
    fake = _FakeSession(post_code=0x44)
    coordinator._session = fake

    writes = [
        {"href": "/mode/vs/0", "payload": {"a": 1}},
        {"href": "/washer/vs/0", "payload": {"b": 2}},
        {"href": "/mode/vs/0", "payload": {"a": 3}},
    ]
    response = await _call_write(hass, device_id, writes=writes)

    assert [path for path, _ in fake.post_calls] == [
        ["mode", "vs", "0"],
        ["washer", "vs", "0"],
        ["mode", "vs", "0"],
    ]
    assert [body for _, body in fake.post_calls] == [
        {"a": 1},
        {"b": 2},
        {"a": 3},
    ]
    assert response is not None
    assert response["device_id"] == device_id
    assert len(response["results"]) == 3
    for result, write in zip(response["results"], writes, strict=True):
        assert result["href"] == write["href"]
        assert result["actual_href"] == write["href"]  # MAIN -> identity transform
        assert result["raw_code"] == 0x44
        assert result["code"] == "2.04"
        assert result["accepted"] is True
    coordinator.async_request_refresh.assert_awaited_once()


async def test_write_resource_settle_honored_between_writes(hass, coordinator, device_id):
    """settle waits *before the next write*, not after the last one --
    asserted on the recorded durations rather than a real sleep."""
    fake = _FakeSession()
    coordinator._session = fake

    writes = [
        {"href": "/a/vs/0", "payload": {"x": 1}, "settle": 2},
        {"href": "/b/vs/0", "payload": {"x": 2}, "settle": 5},
        {"href": "/c/vs/0", "payload": {"x": 3}, "settle": 9},
    ]
    with patch(_SLEEP_TARGET, new_callable=AsyncMock) as mock_sleep:
        await _call_write(hass, device_id, writes=writes)

    assert [call.args[0] for call in mock_sleep.call_args_list] == [2.0, 5.0]


async def test_write_resource_holds_the_session_across_settle_by_default(
    hass, coordinator, device_id
):
    """The default keeps the session for the whole sequence so nothing lands
    between two writes -- asserted on the lock's real state during the wait,
    not merely on the flag being accepted."""
    coordinator._session = _FakeSession()
    locked_during_settle = []

    async def _record(_delay):
        locked_during_settle.append(coordinator._session_lock.locked())

    with patch(_SLEEP_TARGET, new=_record):
        await _call_write(
            hass,
            device_id,
            writes=[
                {"href": "/a/vs/0", "payload": {"x": 1}, "settle": 2},
                {"href": "/b/vs/0", "payload": {"x": 2}},
            ],
        )

    assert locked_during_settle == [True]
    # ...and it's handed back afterward, rather than leaked to the next call.
    assert not coordinator._session_lock.locked()


async def test_write_resource_releases_the_session_across_settle_when_asked(
    hass, coordinator, device_id
):
    """hold_session_lock=False frees the session across the waits, so polls
    and entity writes keep working through a long sequence."""
    coordinator._session = _FakeSession()
    locked_during_settle = []

    async def _record(_delay):
        locked_during_settle.append(coordinator._session_lock.locked())

    with patch(_SLEEP_TARGET, new=_record):
        response = await _call_write(
            hass,
            device_id,
            writes=[
                {"href": "/a/vs/0", "payload": {"x": 1}, "settle": 2},
                {"href": "/b/vs/0", "payload": {"x": 2}},
            ],
            hold_session_lock=False,
        )

    assert locked_during_settle == [False]
    assert not coordinator._session_lock.locked()
    # Both writes still go out, in order -- only the locking differs.
    assert [r["href"] for r in response["results"]] == ["/a/vs/0", "/b/vs/0"]


async def test_write_resource_changed_true_when_readback_matches_payload(
    hass, coordinator, device_id
):
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "target"})
    coordinator._session = fake

    response = await _call_write(
        hass, device_id, writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}]
    )

    assert response["results"][0]["changed"] is True


async def test_write_resource_changed_false_when_readback_differs(hass, coordinator, device_id):
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "unchanged"})
    coordinator._session = fake

    response = await _call_write(
        hass, device_id, writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}]
    )

    assert response["results"][0]["changed"] is False


# ----------------------------------------------------------------------
# response_body / readback: the two knobs the oven cycle-start probes need
# (docs/investigations/oven-cycle-start.md)
# ----------------------------------------------------------------------


async def test_write_resource_surfaces_the_post_response_body(hass, coordinator, device_id):
    """The POST's own body used to go on the floor (`code, _ = sess.post(...)`).
    The fridge answers with a verbatim echo worth nothing, but the laundry
    firmware answers `"Control fail, <...>"` -- a board's stated reason for
    refusing a write is worth more than the 2.04 that carries it."""
    fake = _FakeSession(post_response={"error": "Control fail, Not Support"})
    fake.queue_get("mode/vs/0", {"x.field": "original"})
    coordinator._session = fake

    response = await _call_write(
        hass, device_id, writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}]
    )

    assert response["results"][0]["response_body"] == {"error": "Control fail, Not Support"}


async def test_write_resource_response_body_is_none_when_the_board_answers_empty(
    hass, coordinator, device_id
):
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "original"})
    coordinator._session = fake

    response = await _call_write(
        hass, device_id, writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}]
    )

    assert response["results"][0]["response_body"] is None


async def test_write_resource_readback_false_skips_the_follow_up_get(hass, coordinator, device_id):
    """`smartthings-local` reports that on some boards the fetch-back GET is
    itself what triggers the revert, so a probe has to be able to write
    without one. Per write, not per call: the second write here keeps its
    readback and is the only GET on the wire."""
    fake = _FakeSession()
    fake.queue_get("b/vs/0", {"x": 2})
    coordinator._session = fake

    response = await _call_write(
        hass,
        device_id,
        writes=[
            {"href": "/a/vs/0", "payload": {"x": 1}, "readback": False},
            {"href": "/b/vs/0", "payload": {"x": 2}},
        ],
    )

    assert fake.get_calls == [["b", "vs", "0"]]
    skipped, kept = response["results"]
    assert skipped["readback"] is False
    assert skipped["after"] == {}
    # None, not False: nothing was compared, which is not the same claim as
    # "the payload isn't there".
    assert skipped["changed"] is None
    assert kept["readback"] is True
    assert kept["changed"] is True


async def test_write_resource_readback_false_still_verifies_after(hass, coordinator, device_id):
    """With no immediate read, verify_after's delayed one is the only check
    left -- which is the whole point of being able to skip the other."""
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "target"})
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new_callable=AsyncMock):
        response = await _call_write(
            hass,
            device_id,
            writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}, "readback": False}],
            verify_after=30,
        )

    assert fake.get_calls == [["mode", "vs", "0"]]
    assert response["results"][0]["changed"] is None
    assert response["verified"]["/mode/vs/0"]["held"] is True


# ----------------------------------------------------------------------
# verify_after: the delayed re-read this feature exists for (issue #300)
# ----------------------------------------------------------------------


async def test_write_resource_verify_after_reports_held(hass, coordinator, device_id):
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "target"})  # write's own follow-up read
    fake.queue_get("mode/vs/0", {"x.field": "target"})  # verify_after re-read: held
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new_callable=AsyncMock) as mock_sleep:
        response = await _call_write(
            hass,
            device_id,
            writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}],
            verify_after=30,
        )

    assert 30.0 in [call.args[0] for call in mock_sleep.call_args_list]
    verified = response["verified"]["/mode/vs/0"]
    assert verified["held"] is True
    assert verified["rep"] == {"x.field": "target"}
    assert verified["read_error"] is None


async def test_write_resource_verify_after_reports_reverted(hass, coordinator, device_id):
    """Issue #300's own symptom: a Samsung wall oven board answers 2.04
    Changed to a settings write while idle and then silently reverts it
    once the follow-up read has already come back clean. verify_after's
    whole purpose is catching exactly this -- `changed` (immediate) and
    `held` (delayed) must be able to disagree."""
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "target"})  # looks accepted right after the write
    fake.queue_get("mode/vs/0", {"x.field": "original"})  # reverted by the time verify_after fires
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new_callable=AsyncMock):
        response = await _call_write(
            hass,
            device_id,
            writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}],
            verify_after=30,
        )

    assert response["results"][0]["changed"] is True
    verified = response["verified"]["/mode/vs/0"]
    assert verified["held"] is False
    assert verified["rep"] == {"x.field": "original"}


async def test_write_resource_verify_after_survives_a_failed_confirmation_read(
    hass, coordinator, device_id, monkeypatch
):
    """The write itself already landed (see `results`, built before
    verify_after's wait even starts) by the time the confirmation read runs
    -- a session dying in the gap verify_after waits out (smartthings-local's
    redacted SessionClosedError/SessionTimeoutError, or any other exception)
    must not lose that outcome behind a raised exception. Same "couldn't
    verify" posture as a 4.04/empty read: `held` stays None, not False."""
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "target"})  # write's own follow-up read
    coordinator._session = fake

    def _boom(path_segs, href, query=()):
        raise ConnectionError("session closed")

    monkeypatch.setattr(coordinator, "_raw_read_blocking", _boom)

    with patch(_SLEEP_TARGET, new_callable=AsyncMock):
        response = await _call_write(
            hass,
            device_id,
            writes=[{"href": "/mode/vs/0", "payload": {"x.field": "target"}}],
            verify_after=30,
        )

    # The write's own results survive even though verification blew up.
    assert response["results"][0]["accepted"] is True
    verified = response["verified"]["/mode/vs/0"]
    assert verified["held"] is None
    assert verified["rep"] == {}
    # raw_code 0 alone is indistinguishable from a real 4.04 -- read_error
    # is what tells a caller this was an unreachable session, not a reply.
    assert verified["read_error"] == "session closed"


async def test_write_resource_no_verified_key_when_verify_after_is_zero(
    hass, coordinator, device_id
):
    fake = _FakeSession()
    coordinator._session = fake

    response = await _call_write(
        hass, device_id, writes=[{"href": "/mode/vs/0", "payload": {"x": 1}}]
    )

    assert "verified" not in response


# ----------------------------------------------------------------------
# Device target resolution
# ----------------------------------------------------------------------


async def test_write_resource_zero_devices_rejected(hass, coordinator):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_WRITE_RESOURCE,
            {"writes": [{"href": "/a/vs/0", "payload": {"x": 1}}]},
            blocking=True,
            return_response=True,
        )


async def test_write_resource_two_devices_rejected(hass, coordinator, device_id):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_WRITE_RESOURCE,
            {"writes": [{"href": "/a/vs/0", "payload": {"x": 1}}]},
            target={"device_id": [device_id, "some-other-unrelated-device-id"]},
            blocking=True,
            return_response=True,
        )


async def test_write_resource_unresolvable_device_rejected(hass, coordinator):
    """The device exists in the registry but no loaded coordinator claims
    it -- e.g. a device belonging to a different integration entirely."""
    dev_reg = dr.async_get(hass)
    other_entry = MockConfigEntry(domain="other_domain")
    other_entry.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=other_entry.entry_id,
        identifiers={("other_domain", "unrelated")},
    )
    with pytest.raises(ServiceValidationError):
        await _call_write(hass, device.id, writes=[{"href": "/a/vs/0", "payload": {"x": 1}}])


# ----------------------------------------------------------------------
# Subdevice href translation (issue #177)
# ----------------------------------------------------------------------


async def test_write_resource_translates_href_for_indexed_subdevice(hass, coordinator):
    sub = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator.subdevices = [sub]
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers=coordinator.device_info_for(sub)["identifiers"],
    )
    fake = _FakeSession()
    coordinator._session = fake

    response = await _call_write(
        hass, device.id, writes=[{"href": "/mode/vs/0", "payload": {"x": 1}}]
    )

    assert fake.post_calls[0][0] == ["mode", "vs", "1"]
    result = response["results"][0]
    assert result["href"] == "/mode/vs/0"
    assert result["actual_href"] == "/mode/vs/1"


async def test_write_resource_normalizes_href_before_subdevice_translation(hass, coordinator):
    """A trailing slash must not silently retarget the write at the master.

    `Subdevice.to_actual` is a textual transform that rewrites only a
    trailing '0' segment, so '/mode/vs/0/' passes through it unchanged and
    then normalizes downstream to the master's '/mode/vs/0' -- landing on
    the wrong cavity while still answering 2.04. Nothing in the response
    would have given that away.
    """
    sub = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator.subdevices = [sub]
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers=coordinator.device_info_for(sub)["identifiers"],
    )
    fake = _FakeSession()
    coordinator._session = fake

    response = await _call_write(
        hass, device.id, writes=[{"href": "/mode/vs/0/", "payload": {"x": 1}}]
    )

    assert fake.post_calls[0][0] == ["mode", "vs", "1"]
    result = response["results"][0]
    assert result["href"] == "/mode/vs/0"
    assert result["actual_href"] == "/mode/vs/1"


async def test_write_resource_verified_is_keyed_by_canonical_href_for_subdevice(hass, coordinator):
    """`verified` promises canonical keys; the coordinator reports actual
    ones, so the translation back has to line up with what was sent."""
    sub = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator.subdevices = [sub]
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers=coordinator.device_info_for(sub)["identifiers"],
    )
    fake = _FakeSession()
    fake.queue_get("/mode/vs/1", {"x": 1})
    coordinator._session = fake

    response = await _call_write(
        hass,
        device.id,
        writes=[{"href": "/mode/vs/0", "payload": {"x": 1}}],
        verify_after=1,
    )

    assert list(response["verified"]) == ["/mode/vs/0"]
    assert response["verified"]["/mode/vs/0"]["held"] is True


async def test_write_resource_failure_midway_names_the_writes_that_landed(
    hass, coordinator, device_id
):
    """A drop partway through leaves the appliance holding a partial
    sequence; the error has to say how far it got, or the operator can't
    tell what state the device is in without starting over blind."""

    class _DropsOnSecondWrite(_FakeSession):
        def write(self, path_segs, body, timeout=None):
            if len(self.post_calls) == 1:
                raise OSError("session dropped")
            return super().write(path_segs, body, timeout)

    coordinator._session = _DropsOnSecondWrite()

    with pytest.raises(HomeAssistantError) as excinfo:
        await _call_write(
            hass,
            device_id,
            writes=[
                {"href": "/mode/vs/0", "payload": {"a": 1}},
                {"href": "/temperatures/vs/0", "payload": {"b": 2}},
                {"href": "/operational/state/vs/0", "payload": {"c": 3}},
            ],
        )

    message = str(excinfo.value)
    assert "1 of 3" in message
    assert "/mode/vs/0" in message
    # Entities still get pulled back in line with whatever did land.
    coordinator.async_request_refresh.assert_awaited()


async def test_write_resource_held_is_none_when_the_verify_read_fails(hass, coordinator, device_id):
    """A re-read that doesn't come back is not evidence of a revert.

    _raw_read_blocking answers a non-2.05 with an empty rep, and every
    payload comparison against {} is False -- so without this, a 4.04 or a
    dropped verify read is reported as `held: false`, indistinguishable
    from the board quietly putting the old value back. That distinction is
    the whole reason verify_after exists (issue #300).
    """

    class _FailingVerifyRead(_FakeSession):
        def read(self, path_segs, timeout=None, query=()):
            self.get_calls.append(list(path_segs))
            # The write's own follow-up read succeeds; the later verify
            # re-read of the same href is the one that fails.
            if len(self.get_calls) == 1:
                return 0x45, {"x": 1}
            return 0x84, None

    coordinator._session = _FailingVerifyRead()

    response = await _call_write(
        hass,
        device_id,
        writes=[{"href": "/mode/vs/0", "payload": {"x": 1}}],
        verify_after=1,
    )

    verified = response["verified"]["/mode/vs/0"]
    assert verified["held"] is None
    assert verified["code"] == "4.04"
    # The immediate readback still saw the value land -- only the delayed
    # confirmation is unknown, and the two must not be conflated.
    assert response["results"][0]["changed"] is True


# ----------------------------------------------------------------------
# Validation caps
# ----------------------------------------------------------------------


async def test_write_resource_rejects_more_than_ten_writes(hass, coordinator, device_id):
    writes = [{"href": f"/x/vs/{i}", "payload": {"a": i}} for i in range(11)]
    with pytest.raises(ServiceValidationError):
        await _call_write(hass, device_id, writes=writes)


async def test_write_resource_rejects_settle_above_thirty_seconds(hass, coordinator, device_id):
    with pytest.raises(ServiceValidationError):
        await _call_write(
            hass,
            device_id,
            writes=[{"href": "/a/vs/0", "payload": {"x": 1}, "settle": 31}],
        )


async def test_write_resource_rejects_empty_payload(hass, coordinator, device_id):
    with pytest.raises(ServiceValidationError):
        await _call_write(hass, device_id, writes=[{"href": "/a/vs/0", "payload": {}}])


async def test_write_resource_rejects_non_dict_payload(hass, coordinator, device_id):
    with pytest.raises(ServiceValidationError):
        await _call_write(
            hass, device_id, writes=[{"href": "/a/vs/0", "payload": ["not", "a", "dict"]}]
        )


async def test_write_resource_rejects_empty_href(hass, coordinator, device_id):
    with pytest.raises(ServiceValidationError):
        await _call_write(hass, device_id, writes=[{"href": "", "payload": {"x": 1}}])


# ----------------------------------------------------------------------
# read_resource
# ----------------------------------------------------------------------


async def test_read_resource_with_href_does_live_get(hass, coordinator, device_id):
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "live"})
    coordinator._session = fake

    response = await _call_read(hass, device_id, href="/mode/vs/0")

    assert fake.get_calls == [["mode", "vs", "0"]]
    assert response["href"] == "/mode/vs/0"
    assert response["actual_href"] == "/mode/vs/0"
    assert response["rep"] == {"x.field": "live"}
    # No duplicate copy of a Property map that `rep` already carries.
    assert "body" not in response


async def test_read_resource_sends_an_hrefs_query_as_uri_query_options(
    hass, coordinator, device_id
):
    """A /device/<n> Collection answers its links unless asked for the batch
    interface, so the query has to reach the GET rather than the path."""
    batch = [{"rt": ["oic.wk.col"]}, {"href": "/mode/vs/0", "rep": {"x.field": "live"}}]
    fake = _FakeSession()
    fake.queue_get("device/1", batch)
    coordinator._session = fake

    response = await _call_read(hass, device_id, href="/device/1?if=oic.if.b&x=y")

    assert fake.get_calls == [["device", "1"]]
    assert fake.get_queries == [["if=oic.if.b", "x=y"]]
    assert response["href"] == "/device/1"
    assert response["query"] == ["if=oic.if.b", "x=y"]
    assert response["body"] == batch


async def test_read_resource_percent_decodes_the_query_and_drops_a_fragment(
    hass, coordinator, device_id
):
    """URI-Query options carry decoded text (RFC 7252 §6.4)."""
    fake = _FakeSession()
    coordinator._session = fake

    await _call_read(hass, device_id, href="/mode/vs/0?rt=x.a%2Cx.b%26c#frag")

    assert fake.get_queries == [["rt=x.a,x.b&c"]]


async def test_read_resource_with_a_query_the_library_refuses_keeps_the_session(
    hass, coordinator, device_id, monkeypatch
):
    """The library validates a query before sending anything, so refusing a
    typo is no evidence the session is dead."""
    fake = _FakeSession()
    coordinator._session = fake

    def _refuse(path_segs, href, query=()):
        raise ValueError("query must contain at most 32 values")

    monkeypatch.setattr(coordinator, "_raw_read_blocking", _refuse)

    with pytest.raises(HomeAssistantError):
        await _call_read(hass, device_id, href="/device/1?" + "&".join(["a=b"] * 33))

    assert coordinator._session is fake


async def test_read_resource_under_a_query_leaves_the_cache_alone(hass, coordinator, device_id):
    """A baseline or filtered interface can return a different property set
    from the plain rep entities are built on."""
    fake = _FakeSession()
    fake.queue_get("mode/vs/0", {"x.field": "baseline", "rt": ["x.com.samsung.da.mode"]})
    coordinator._session = fake
    coordinator._observe.apply("/mode/vs/0", {"x.field": "cached"}, source="poll")

    response = await _call_read(hass, device_id, href="/mode/vs/0?if=oic.if.baseline")

    assert response["rep"]["x.field"] == "baseline"
    assert coordinator.device_resources(MAIN)["/mode/vs/0"] == {"x.field": "cached"}


async def test_read_resource_surfaces_a_collections_list_body(hass, coordinator, device_id):
    """A Collection answers a CBOR list, not a Property map, so `rep` can't
    hold it (issue #335: `/sec/devices` came back as an accepted-but-empty
    2.05, which reads as "exists, nothing in it" -- the opposite of what a
    populated batch means)."""
    batch = [
        {"rt": ["x.com.samsung.devcol", "oic.wk.col"]},
        {"href": "/mode/vs/0", "rep": {"x.field": "live"}},
    ]
    fake = _FakeSession()
    fake.queue_get("sec/devices", batch)
    coordinator._session = fake

    response = await _call_read(hass, device_id, href="/sec/devices")

    assert response["code"] == "2.05"
    assert response["rep"] == {}
    assert response["body"] == batch


async def test_read_resource_renders_a_binary_rep_as_a_marker(hass, coordinator, device_id):
    """A CBOR byte string has no JSON form, and a service response is
    JSON-serialized -- so without registry/encode.py the appliance's usage
    history at /file/transfer/vs/0 (issue #301) could not leave the device
    through this service at all."""
    blob = bytes(range(256)) * 8
    fake = _FakeSession()
    fake.queue_get(
        "file/transfer/vs/0",
        {"x.com.samsung.name": "/mnt/usage.db", "x.com.samsung.blob": blob},
    )
    coordinator._session = fake

    response = await _call_read(hass, device_id, href="/file/transfer/vs/0")

    assert response["rep"]["x.com.samsung.name"] == "/mnt/usage.db"
    marker = response["rep"]["x.com.samsung.blob"]
    assert marker[TYPE_KEY] == "bytes"
    assert marker["len"] == len(blob)
    assert marker["sha256"] == hashlib.sha256(blob).hexdigest()
    # The response is what a contributor pastes into a fixture, so the trip
    # back to real bytes has to survive an actual JSON encode.
    assert from_json_safe(json.loads(json.dumps(response)))["rep"]["x.com.samsung.blob"] == blob


async def test_read_resource_failure_is_surfaced_as_a_home_assistant_error(
    hass, coordinator, device_id, monkeypatch
):
    """A session/network failure during a live debug read (e.g.
    smartthings-local's redacted SessionError, or any other exception) must
    not reach the service caller raw and untranslated -- write_resource
    already goes through HomeAssistantError on failure, and async_raw_read
    must match that instead of letting the exception escape uncaught."""

    def _boom(path_segs, href, query=()):
        raise ConnectionError("session closed")

    monkeypatch.setattr(coordinator, "_raw_read_blocking", _boom)

    with pytest.raises(HomeAssistantError):
        await _call_read(hass, device_id, href="/mode/vs/0")


async def test_read_resource_failure_closes_a_confirmed_dead_session(
    hass, coordinator, device_id, monkeypatch
):
    """A non-timeout failure is unambiguous (same TimeoutError-vs-anything-
    else split as _poll_once) -- leaving a confirmed-dead session installed
    would fail every subsequent read/write identically until the next real
    poll cycle's own reconnect notices, up to a full update_interval later."""
    coordinator._session = _FakeSession()

    def _boom(path_segs, href, query=()):
        raise ConnectionError("session closed")

    monkeypatch.setattr(coordinator, "_raw_read_blocking", _boom)

    with pytest.raises(HomeAssistantError):
        await _call_read(hass, device_id, href="/mode/vs/0")

    assert coordinator._session is None


async def test_read_resource_timeout_does_not_close_the_session(
    hass, coordinator, device_id, monkeypatch
):
    """The other half of the same distinction: a block-ACK TimeoutError
    alone doesn't prove the session is dead (see _poll_once), so unlike
    any other failure it must not tear down a session that might still be
    perfectly fine."""
    fake = _FakeSession()
    coordinator._session = fake

    def _boom(path_segs, href, query=()):
        raise TimeoutError("GET timeout")

    monkeypatch.setattr(coordinator, "_raw_read_blocking", _boom)

    with pytest.raises(HomeAssistantError):
        await _call_read(hass, device_id, href="/mode/vs/0")

    assert coordinator._session is fake


async def test_read_resource_without_href_returns_cached_snapshot_and_does_not_get(
    hass, coordinator, device_id
):
    fake = _FakeSession()
    coordinator._session = fake
    # Seed the cache the way a poll would -- the fake session's `get` is
    # never touched by this test.
    coordinator._observe.apply("/mode/vs/0", {"x.field": "cached"}, source="poll")

    response = await _call_read(hass, device_id)

    assert fake.get_calls == []
    assert response["resources"]["/mode/vs/0"] == {"x.field": "cached"}


async def test_read_resource_snapshot_is_redacted_like_diagnostics(hass, coordinator, device_id):
    coordinator._session = _FakeSession()
    coordinator._observe.apply(
        "/information/vs/0",
        {"x.com.samsung.email": "someone@example.com", "x.field": "kept"},
        source="poll",
    )
    coordinator._observe.apply(
        "/wificonf", {"tnn": "ExampleNet", "cd": "passphrase"}, source="poll"
    )

    response = await _call_read(hass, device_id)

    assert response["resources"]["/information/vs/0"] == {
        "x.com.samsung.email": "**REDACTED**",
        "x.field": "kept",
    }
    assert response["resources"]["/wificonf"] == {"tnn": "**REDACTED**", "cd": "**REDACTED**"}


# ----------------------------------------------------------------------
# The raw actions are admin-only
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "data"),
    [
        (SERVICE_READ_RESOURCE, {}),
        (SERVICE_WRITE_RESOURCE, {"writes": [{"href": "/mode/vs/0", "payload": {"x": 1}}]}),
        (SERVICE_OBSERVE_RESOURCE, {"href": "/mode/vs/0", "duration": 1}),
    ],
)
async def test_raw_actions_refuse_a_non_admin_user(
    hass, coordinator, device_id, hass_read_only_user, service, data
):
    fake = _FakeSession()
    coordinator._session = fake

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            service,
            data,
            target={"device_id": device_id},
            blocking=True,
            return_response=True,
            context=Context(user_id=hass_read_only_user.id),
        )

    assert fake.get_calls == []
    assert fake.post_calls == []


async def test_read_resource_refuses_an_unknown_user(hass, coordinator, device_id):
    coordinator._session = _FakeSession()

    with pytest.raises(UnknownUser):
        await _call_read_as(hass, device_id, Context(user_id="no-such-user"))


async def test_read_resource_still_answers_an_admin_user(
    hass, coordinator, device_id, hass_admin_user
):
    coordinator._session = _FakeSession()
    coordinator._observe.apply("/mode/vs/0", {"x.field": "cached"}, source="poll")

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_READ_RESOURCE,
        {},
        target={"device_id": device_id},
        blocking=True,
        return_response=True,
        context=Context(user_id=hass_admin_user.id),
    )

    assert response["resources"]["/mode/vs/0"] == {"x.field": "cached"}


# ----------------------------------------------------------------------
# Options-flow debug panel, rewired onto write_resource (issue #300)
# ----------------------------------------------------------------------


async def test_options_flow_debug_write_goes_through_write_resource_service(
    hass, coordinator, device_id
):
    """Drives LocalThingsOptionsFlow's real step methods directly (rather
    than through hass.config_entries.options.async_init/async_configure,
    which needs the integration resolvable through HA's loader -- not
    exercised by any test in this repo and orthogonal to what this test is
    actually checking): the panel's async_step_debug_edit must call the
    write_resource service and get back a result the rest of the flow can
    render, not coord.async_raw_write directly."""
    fake = _FakeSession(post_code=0x44)
    fake.queue_get("course/vs/0", {"x.field": "after"})
    coordinator._session = fake

    flow = LocalThingsOptionsFlow()
    flow.hass = hass
    flow.handler = coordinator._entry.entry_id

    write_result = await flow.async_step_debug_write({"href": "/course/vs/0"})
    assert write_result["step_id"] == "debug_edit"

    edit_result = await flow.async_step_debug_edit({"payload": {"x.com.samsung.da.field": "value"}})
    assert edit_result["step_id"] == "debug_result"
    placeholders = edit_result["description_placeholders"]
    assert placeholders is not None
    assert placeholders["code"] == "2.04 (0x44)"

    assert len(fake.post_calls) == 1
    posted_path, posted_body = fake.post_calls[0]
    assert posted_path == ["course", "vs", "0"]
    assert posted_body == {"x.com.samsung.da.field": "value"}

    assert len(fake.post_calls) == 1
    posted_path, posted_body = fake.post_calls[0]
    assert posted_path == ["course", "vs", "0"]
    assert posted_body == {"x.com.samsung.da.field": "value"}


# ----------------------------------------------------------------------
# write_resource: OCF Collection batch payloads (issue #473)
# ----------------------------------------------------------------------

# The cook start measured on an NV7000BS wall oven -- see
# docs/investigations/oven-cycle-start.md. Kept whole rather than reduced
# to a minimal example: the point of the batch path is that this exact
# payload can be reproduced on other hardware, and a test that writes a
# simplified stand-in would not catch a regression that mangles the
# marker element or reorders the rest.
_MEASURED_COOK_START = [
    {"href": "/devices/0"},
    {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Defrost"]}},
    {
        "href": "/temperatures/vs/0",
        "rep": {
            "x.com.samsung.da.items": [
                {
                    "x.com.samsung.da.desired": "30",
                    "x.com.samsung.da.id": "0",
                    "x.com.samsung.da.unit": "Celsius",
                }
            ]
        },
    },
    {
        "href": "/operational/state/vs/0",
        "rep": {
            "x.com.samsung.da.operationTime": "00:01:00",
            "x.com.samsung.da.state": "Run",
        },
    },
]


async def test_write_resource_posts_a_collection_batch_byte_for_byte(hass, coordinator, device_id):
    """The payload is the experiment, so it goes on the wire exactly as
    given -- element order, the rep-less `/devices/0` marker and all."""
    fake = _FakeSession()
    coordinator._session = fake

    await _call_write(
        hass,
        device_id,
        writes=[{"href": "/device/0", "payload": _MEASURED_COOK_START, "readback": False}],
    )

    posted_path, posted_body = fake.post_calls[0]
    assert posted_path == ["device", "0"]
    assert posted_body == _MEASURED_COOK_START


async def test_write_resource_never_rewrites_a_batchs_inner_hrefs_for_a_subdevice(
    hass, coordinator
):
    """A subdevice rewrites the write's own href and nothing else.

    Translating the inner ones would be indistinguishable from the caller
    having typed them that way, which destroys the permutation being
    tested: whether a composite board wants its cavity's index inside the
    batch, or the canonical spelling, is the open question.
    """
    sub = Subdevice(kind="indexed", key="1", seed_path=("device", "1"))
    coordinator.subdevices = [sub]
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers=coordinator.device_info_for(sub)["identifiers"],
    )
    fake = _FakeSession()
    coordinator._session = fake

    response = await _call_write(
        hass,
        device.id,
        writes=[{"href": "/device/0", "payload": _MEASURED_COOK_START, "readback": False}],
    )

    posted_path, posted_body = fake.post_calls[0]
    assert posted_path == ["device", "1"]
    assert posted_body == _MEASURED_COOK_START
    assert response["results"][0]["actual_href"] == "/device/1"


async def test_write_resource_batch_readback_reports_the_named_resources(
    hass, coordinator, device_id
):
    """A Collection answers a batch, not a Property map, so `after` is that
    batch parsed to {href: rep} -- the state of what the write named."""
    fake = _FakeSession()
    fake.queue_get(
        "device/0",
        [
            {"x.com.samsung.devcol": "collection"},
            {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Defrost"]}},
            {
                "href": "/operational/state/vs/0",
                "rep": {
                    "x.com.samsung.da.operationTime": "00:01:00",
                    "x.com.samsung.da.state": "Run",
                },
            },
        ],
    )
    coordinator._session = fake

    response = await _call_write(
        hass,
        device_id,
        writes=[{"href": "/device/0", "payload": _MEASURED_COOK_START}],
    )

    result = response["results"][0]
    assert result["after"]["/mode/vs/0"] == {"x.com.samsung.da.modes": ["Defrost"]}
    # The temperatures element is missing from the readback, so not
    # everything the batch wrote is present.
    assert result["changed"] is False


async def test_write_resource_batch_changed_true_when_every_element_reads_back(
    hass, coordinator, device_id
):
    fake = _FakeSession()
    fake.queue_get(
        "device/0", [{"href": e["href"], "rep": e["rep"]} for e in _MEASURED_COOK_START[1:]]
    )
    coordinator._session = fake

    response = await _call_write(
        hass,
        device_id,
        writes=[{"href": "/device/0", "payload": _MEASURED_COOK_START}],
    )

    assert response["results"][0]["changed"] is True


async def test_write_resource_batch_verify_after_reports_held_per_element(
    hass, coordinator, device_id
):
    """The result this path exists for: a batch whose mode stuck and whose
    run command was discarded is a different finding from a flat refusal,
    and only a per-element answer tells them apart."""
    fake = _FakeSession()
    fake.queue_get(
        "device/0",
        [
            {"href": "/mode/vs/0", "rep": {"x.com.samsung.da.modes": ["Defrost"]}},
            {
                "href": "/operational/state/vs/0",
                "rep": {
                    "x.com.samsung.da.operationTime": "00:00:00",
                    "x.com.samsung.da.state": "Ready",
                },
            },
            {
                "href": "/temperatures/vs/0",
                "rep": {
                    "x.com.samsung.da.items": [
                        {
                            "x.com.samsung.da.desired": "30",
                            "x.com.samsung.da.id": "0",
                            "x.com.samsung.da.unit": "Celsius",
                        }
                    ]
                },
            },
        ],
    )
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new_callable=AsyncMock):
        response = await _call_write(
            hass,
            device_id,
            writes=[{"href": "/device/0", "payload": _MEASURED_COOK_START, "readback": False}],
            verify_after=20,
        )

    verified = response["verified"]["/device/0"]
    assert verified["held"] is False
    assert verified["elements"] == {
        "/mode/vs/0": True,
        "/temperatures/vs/0": True,
        "/operational/state/vs/0": False,
    }
    # The rep-less marker is not a write, so nothing claims anything about it.
    assert "/devices/0" not in verified["elements"]


# ----------------------------------------------------------------------
# observe_resource (issue #607): a timed OBSERVE next to a plain GET
# ----------------------------------------------------------------------

_OBS_HREF = "/energy/consumption/vs/0"
_OBS_SEGS = ["energy", "consumption", "vs", "0"]
_OBS_GET = {
    "x.com.samsung.da.cumulativePower": "297609",
    "x.com.samsung.da.instantaneousPowerUnit": "W",
}
_OBS_PUSH = {**_OBS_GET, "x.com.samsung.da.instantaneousPower": "450"}


class _ObservingSession(_FakeSession):
    """Records subscribe/unsubscribe, and pushes `on_subscribe` through the
    coordinator's own notification callback the way the DTLS reader would."""

    def __init__(self, coord, on_subscribe=()):
        super().__init__()
        self._coord = coord
        self._on_subscribe = list(on_subscribe)
        self.subscribed: list[tuple[list[str], tuple]] = []
        self.unsubscribed: list[list[str]] = []

    def subscribe(self, path_segs, *, query=()):
        self.subscribed.append((list(path_segs), tuple(query)))
        for rep in self._on_subscribe:
            self.push("/" + "/".join(path_segs), rep)
        return b"\x01"

    def unsubscribe(self, path_segs):
        self.unsubscribed.append(list(path_segs))
        return 1

    def push(self, href, rep):
        payload = rep if isinstance(rep, bytes) else cbor2.dumps(rep)
        self._coord._observe.on_notification(href, payload)


async def _call_observe(hass: HomeAssistant, device: str, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_OBSERVE_RESOURCE,
        data,
        target={"device_id": device},
        blocking=True,
        return_response=True,
    )


async def _no_sleep(_delay):
    return None


async def test_a_field_only_a_push_carries_is_named(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator, on_subscribe=[_OBS_PUSH])
    fake.queue_get(_OBS_HREF, _OBS_GET)
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href=_OBS_HREF, duration=10)

    assert fake.get_calls == [_OBS_SEGS]
    assert fake.subscribed == [(_OBS_SEGS, ())]
    assert fake.unsubscribed == [_OBS_SEGS]
    assert response["get"] == {"code": "2.05", "rep": _OBS_GET}
    assert [n["rep"] for n in response["notifications"]] == [_OBS_PUSH]
    assert response["notifications"][0]["t"] >= 0
    assert response["fields_only_in_notifications"] == ["x.com.samsung.da.instantaneousPower"]
    assert response["already_observed"] is False
    assert response["session_dropped"] is False


async def test_nothing_pushed_is_an_empty_list(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    fake.queue_get(_OBS_HREF, _OBS_GET)
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert response["notifications"] == []
    assert response["fields_only_in_notifications"] == []


async def test_a_query_is_sent_with_the_subscription(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href=f"{_OBS_HREF}?if=oic.if.baseline")

    assert fake.subscribed == [(_OBS_SEGS, ("if=oic.if.baseline",))]
    assert response["query"] == ["if=oic.if.baseline"]


async def test_an_href_the_coordinator_observes_is_only_listened_to(hass, coordinator, device_id):
    """unsubscribe retires every relation on the path, so touching it would
    end the coordinator's own push updates."""
    fake = _ObservingSession(coordinator)
    coordinator._session = fake
    coordinator._observe.subscribed_hrefs = {_OBS_HREF}

    async def _push_while_waiting(_delay):
        fake.push(_OBS_HREF, _OBS_PUSH)

    with patch(_SLEEP_TARGET, new=_push_while_waiting):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert fake.subscribed == []
    assert fake.unsubscribed == []
    assert response["already_observed"] is True
    assert [n["rep"] for n in response["notifications"]] == [_OBS_PUSH]


async def test_a_relation_the_coordinator_took_over_is_left_alone(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    async def _coordinator_subscribes(_delay):
        coordinator._observe.subscribed_hrefs = {_OBS_HREF}

    with patch(_SLEEP_TARGET, new=_coordinator_subscribes):
        await _call_observe(hass, device_id, href=_OBS_HREF)

    assert fake.unsubscribed == []


async def test_a_session_lost_while_listening_is_reported(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    async def _drop(_delay):
        coordinator._session = None

    with patch(_SLEEP_TARGET, new=_drop):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert response["session_dropped"] is True
    assert fake.unsubscribed == []


async def test_a_push_that_will_not_decode_is_kept_raw(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator, on_subscribe=[b"\xff\x00"])
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert response["notifications"][0]["rep"][TYPE_KEY] == "bytes"
    assert response["fields_only_in_notifications"] == []


async def test_the_listening_stops_after_the_call(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)
    fake.push(_OBS_HREF, _OBS_PUSH)

    assert response["notifications"] == []
    assert coordinator._observe._taps == {}


@pytest.mark.parametrize("duration", [0, 121])
async def test_a_duration_out_of_range_is_refused(hass, coordinator, device_id, duration):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    with pytest.raises(ServiceValidationError) as err:
        await _call_observe(hass, device_id, href=_OBS_HREF, duration=duration)

    assert err.value.translation_key == "observe_duration_out_of_range"
    # Refused before anything reaches the appliance.
    assert fake.get_calls == []
    assert fake.subscribed == []


async def test_a_query_on_an_href_the_coordinator_observes_is_refused(hass, coordinator, device_id):
    coordinator._session = _ObservingSession(coordinator)
    coordinator._observe.subscribed_hrefs = {_OBS_HREF}

    with pytest.raises(ServiceValidationError) as err:
        await _call_observe(hass, device_id, href=f"{_OBS_HREF}?if=oic.if.baseline")

    assert err.value.translation_key == "observe_query_while_observed"


async def test_an_8888_appliance_is_refused(hass, coordinator, device_id):
    entry = coordinator._entry
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_TRANSPORT: TRANSPORT_LEGACY_HTTP}
    )
    coordinator._session = _ObservingSession(coordinator)

    with pytest.raises(ServiceValidationError) as err:
        await _call_observe(hass, device_id, href=_OBS_HREF)

    assert err.value.translation_key == "observe_not_supported"


async def test_a_failed_subscribe_is_a_home_assistant_error(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    with (
        patch.object(fake, "subscribe", side_effect=RuntimeError("socket closed")),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _call_observe(hass, device_id, href=_OBS_HREF)

    assert err.value.translation_key == "debug_observe_failed"
    assert coordinator._observe._taps == {}


async def test_a_relation_the_coordinator_is_still_setting_up_is_left_alone(
    hass, coordinator, device_id
):
    """Between its subscribe burst and committing, the coordinator's hrefs
    aren't in subscribed_hrefs yet; an unsubscribe then would retire its
    fresh relation too."""
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    async def _coordinator_bursts(_delay):
        coordinator._observe.subscribe_hrefs(fake, [_OBS_HREF])

    with patch(_SLEEP_TARGET, new=_coordinator_bursts):
        await _call_observe(hass, device_id, href=_OBS_HREF)

    assert fake.unsubscribed == []


async def test_a_second_call_on_the_same_href_is_refused(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake
    coordinator._observe.reserve_tap(_OBS_HREF, limit=10, isolate=False)

    with pytest.raises(ServiceValidationError) as err:
        await _call_observe(hass, device_id, href=_OBS_HREF)

    assert err.value.translation_key == "observe_in_progress"
    assert fake.get_calls == []
    assert fake.subscribed == []


async def test_a_push_under_a_query_stays_out_of_the_cache(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator, on_subscribe=[_OBS_PUSH])
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href=f"{_OBS_HREF}?if=oic.if.baseline")

    assert [n["rep"] for n in response["notifications"]] == [_OBS_PUSH]
    assert not coordinator._cache.get(_OBS_HREF)


async def test_a_plain_push_reaches_the_cache_as_any_notification_does(
    hass, coordinator, device_id
):
    fake = _ObservingSession(coordinator, on_subscribe=[_OBS_PUSH])
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        await _call_observe(hass, device_id, href=_OBS_HREF)

    assert coordinator._cache.get(_OBS_HREF) == _OBS_PUSH


async def test_a_cancelled_call_still_unsubscribes(hass, coordinator):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake

    async def _cancelled(_delay):
        raise asyncio.CancelledError

    with patch(_SLEEP_TARGET, new=_cancelled), pytest.raises(asyncio.CancelledError):
        await coordinator.async_observe_resource(_OBS_HREF, duration=10)

    assert fake.unsubscribed == [_OBS_SEGS]
    assert coordinator._observe._taps == {}


async def test_a_push_before_the_subscription_is_not_recorded(hass, coordinator, device_id):
    """The coordinator's own relation can push while this call waits for the
    session; that is outside the window asked for."""
    fake = _ObservingSession(coordinator)
    coordinator._session = fake
    coordinator._observe.subscribed_hrefs = {_OBS_HREF}
    read = fake.read

    def _read_then_push(path_segs, timeout=None, query=()):
        fake.push(_OBS_HREF, {"early": True})
        return read(path_segs, timeout=timeout, query=query)

    with (
        patch.object(fake, "read", side_effect=_read_then_push),
        patch(_SLEEP_TARGET, new=_no_sleep),
    ):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert response["notifications"] == []


async def test_the_coordinator_subscribing_during_the_get_means_listen_only(
    hass, coordinator, device_id
):
    fake = _ObservingSession(coordinator)
    coordinator._session = fake
    read = fake.read

    def _read_while_coordinator_subscribes(path_segs, timeout=None, query=()):
        coordinator._observe.subscribed_hrefs = {_OBS_HREF}
        return read(path_segs, timeout=timeout, query=query)

    with (
        patch.object(fake, "read", side_effect=_read_while_coordinator_subscribes),
        patch(_SLEEP_TARGET, new=_no_sleep),
    ):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert fake.subscribed == []
    assert response["already_observed"] is True


async def test_a_collection_get_keeps_its_list_body(hass, coordinator, device_id):
    batch = [{"rt": ["oic.wk.col"]}, {"href": "/power/vs/0", "rep": {"p": "On"}}]
    fake = _ObservingSession(coordinator)
    fake.queue_get("/device/0", batch)
    coordinator._session = fake

    with patch(_SLEEP_TARGET, new=_no_sleep):
        response = await _call_observe(hass, device_id, href="/device/0")

    assert response["get"]["body"] == batch


async def test_notifications_past_the_cap_are_counted_not_kept(hass, coordinator, device_id):
    fake = _ObservingSession(coordinator, on_subscribe=[{"n": i} for i in range(3)])
    coordinator._session = fake

    with (
        patch.object(LocalThingsCoordinator, "_OBSERVE_KEEP", 2),
        patch(_SLEEP_TARGET, new=_no_sleep),
    ):
        response = await _call_observe(hass, device_id, href=_OBS_HREF)

    assert [n["rep"] for n in response["notifications"]] == [{"n": 0}, {"n": 1}]
    assert response["truncated"] is True
