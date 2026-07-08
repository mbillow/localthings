"""Coordinator for Local Things integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import cbor2

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo

from smartthings_local.protocol.dtls_session import DtlsCoapSession

from .registry.batch import parse_device0_batch
from .registry.by_type import for_device
from .registry.discovery import discover, BoundEntity
from .registry import CAPABILITIES
from .registry.adapter import flatten
from .registry.identity import read_identity, DeviceIdentity

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM,
    SUMMARY_INTERVAL_S,
)

_LOGGER = logging.getLogger(__name__)

_SEED_PATH = ['device', '0']


class LocalThingsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages one Samsung appliance: session, discovery, polling."""

    bound: list[BoundEntity]
    device_info: DeviceInfo
    device_serial: str

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=SUMMARY_INTERVAL_S),
        )
        self._entry = entry
        self._session: DtlsCoapSession | None = None
        self._identity: DeviceIdentity | None = None
        self._discovered = False
        self.bound = []
        self._last_resources: dict[str, dict] = {}
        self.device_serial = entry.data[CONF_HOST]  # placeholder until first poll
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_HOST])},
            name=f"Samsung Appliance ({entry.data[CONF_HOST]})",
            manufacturer="Samsung",
        )
        self._session_lock = asyncio.Lock()
        self._subpoll_task: asyncio.Task | None = None
        self._hot_hrefs: list[str] = []
        self._warm_hrefs: list[str] = []

    # ------------------------------------------------------------------
    # Session management (all blocking — must run in executor)
    # ------------------------------------------------------------------

    @property
    def last_resources(self) -> dict:
        return self._last_resources

    def _connect_session(self) -> None:
        host     = self._entry.data[CONF_HOST]
        port     = self._entry.data[CONF_PORT]
        cert_pem = self._entry.data[CONF_LEAF_CERT_PEM]
        key_pem  = self._entry.data[CONF_LEAF_KEY_PEM]

        sess = DtlsCoapSession(host, port, cert_pem=cert_pem, key_pem=key_pem)
        sess.connect()
        sess.start_reader()
        self._session = sess
        _LOGGER.debug("DTLS connected to %s:%d", host, port)
        try:
            self._identity = read_identity(sess, None)
        except Exception as e:
            _LOGGER.debug("read_identity failed: %s", e)
            self._identity = None

    def _close_session(self) -> None:
        sess = self._session
        self._session = None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

    async def async_close(self) -> None:
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None
        await self.hass.async_add_executor_job(self._close_session)

    def _poll_once(self) -> dict[str, dict]:
        """GET /device/0, return parsed resources. Blocking."""
        if self._session is None:
            self._connect_session()
        sess = self._session
        try:
            code, payload = sess.get(_SEED_PATH, timeout=15.0)
        except Exception as e:
            self._close_session()
            raise RuntimeError(f"poll GET failed: {e}") from e
        if code != 0x45 or not payload:
            self._close_session()
            raise RuntimeError(f"poll: unexpected code {code:#04x}")
        try:
            body = cbor2.loads(payload)
        except Exception as e:
            raise RuntimeError(f"poll cbor decode: {e}") from e
        return parse_device0_batch(body) if isinstance(body, list) else {}

    def _poll_hrefs_blocking(self, hrefs: list[str]) -> dict[str, dict]:
        """GET individual hrefs sequentially. Does not reconnect on failure. Blocking."""
        if self._session is None:
            return {}
        results = {}
        first = True
        for href in hrefs:
            if not first:
                self._session.pace()
            first = False
            try:
                path = [s for s in href.strip('/').split('/')]
                code, payload = self._session.get(path, timeout=10.0)
                if code == 0x45 and payload:
                    rep = cbor2.loads(payload)
                    if isinstance(rep, dict):
                        results[href] = rep
            except Exception as e:
                _LOGGER.debug("sub-poll %s: %s", href, e)
        return results

    # ------------------------------------------------------------------
    # Sub-poll loop (runs between summary polls)
    # ------------------------------------------------------------------

    async def _run_subpolls(self) -> None:
        """Poll hot/warm hrefs in the gaps between summary polls.

        Schedule over a SUMMARY_INTERVAL_S window:
          hot hrefs  every slot  (10x, ~3 s apart)
          warm hrefs every other slot (5x, ~6 s apart)
        The lock prevents overlap with the summary poll or a concurrent sub-poll.
        """
        hot = self._hot_hrefs
        warm = self._warm_hrefs
        if not hot and not warm:
            return
        step = SUMMARY_INTERVAL_S / 10  # 3.0 s
        for i in range(1, 10):          # slots 1..9  (T+3 s … T+27 s)
            await asyncio.sleep(step)
            hrefs = list(hot) + (list(warm) if i % 2 == 0 else [])
            async with self._session_lock:
                try:
                    updates = await self.hass.async_add_executor_job(
                        self._poll_hrefs_blocking, hrefs
                    )
                except Exception as e:
                    _LOGGER.debug("sub-poll batch failed: %s", e)
                    continue
            if updates:
                self._last_resources.update(updates)
                self.async_set_updated_data(flatten(self.bound, self._last_resources))

    # ------------------------------------------------------------------
    # Discovery (runs once on first successful poll)
    # ------------------------------------------------------------------

    def _run_discovery(self, resources: dict[str, dict]) -> None:
        one_ui = (resources.get('/otninformation/vs/0', {})
                  .get('swVersionInfo', {})
                  .get('oneUiVersion', ''))
        reg = for_device(one_ui) if one_ui else None
        if reg is not None:
            _LOGGER.debug("device type: %s (oneUiVersion=%r)", reg.name, one_ui)
            bound = discover(resources, reg.capabilities, reg.pattern_capabilities)
        else:
            _LOGGER.warning("unknown device type oneUiVersion=%r; using common caps", one_ui)
            bound = discover(resources, CAPABILITIES)
        self.bound = bound

        serial = (resources.get('/information/vs/0', {})
                  .get('x.com.samsung.da.serialNum', ''))
        if not serial:
            serial = self._entry.data[CONF_HOST]
        self.device_serial = serial

        ident = self._identity
        model = (ident.model if ident else '') or (reg.name.title() if reg else 'Appliance')
        name  = (ident.name  if ident else '') or f"Samsung {model}"
        mfr   = (ident.manufacturer if ident else '') or 'Samsung'

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=name,
            manufacturer=mfr,
            model=model,
        )
        hot, warm = set(), set()
        for be in bound:
            tier = be.capability.poll_tier
            if tier == 'hot':
                hot.add(be.href)
            elif tier == 'warm':
                warm.add(be.href)
        self._hot_hrefs = sorted(hot)
        self._warm_hrefs = sorted(warm)

        self._discovered = True
        _LOGGER.info(
            "discovered %d entities (serial=%s) hot=%s warm=%s",
            len(bound), serial, self._hot_hrefs, self._warm_hrefs,
        )

    # ------------------------------------------------------------------
    # DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        # Stop any in-flight sub-poll before taking the session lock.
        if self._subpoll_task is not None:
            self._subpoll_task.cancel()
            self._subpoll_task = None

        async with self._session_lock:
            try:
                resources = await self.hass.async_add_executor_job(self._poll_once)
            except Exception as e:
                # One reconnect attempt — pause briefly so the device can
                # clean up its DTLS session state before we knock again.
                _LOGGER.warning("poll failed, reconnecting: %s", e)
                await self.hass.async_add_executor_job(self._close_session)
                await asyncio.sleep(5.0)
                try:
                    resources = await self.hass.async_add_executor_job(self._poll_once)
                except Exception as e2:
                    _LOGGER.error("poll failed after reconnect: %s", e2)
                    if self._last_resources:
                        _LOGGER.debug("Full error:", exc_info=e2)
                        return flatten(self.bound, self._last_resources)
                    raise UpdateFailed(f"poll failed after reconnect: {e2}") from e2

        self._last_resources = resources

        if not self._discovered:
            self._run_discovery(resources)

        # Schedule sub-polls for hot/warm hrefs between summary polls.
        if self._hot_hrefs or self._warm_hrefs:
            self._subpoll_task = self.hass.async_create_task(
                self._run_subpolls(), name="localthings_subpoll"
            )

        return flatten(self.bound, resources)

    # ------------------------------------------------------------------
    # Command dispatch (called by entity platforms in Task 5)
    # ------------------------------------------------------------------

    async def async_send_command(self, bound_entity: BoundEntity,
                                 payload: Any) -> None:
        """Write a value to the device. Fire-and-forget style."""
        desc = bound_entity.desc
        write_fn = getattr(desc, 'write_fn', None)
        if write_fn is None:
            return
        href = bound_entity.href
        rep = self._last_resources.get(href or '', {})
        result = write_fn(payload, rep, href)
        if result is None:
            _LOGGER.warning("write_fn rejected payload %r for %s", payload, href)
            return
        path_segs, body = result

        def _do_put():
            sess = self._session
            if sess is None:
                raise RuntimeError("no session")
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=8.0)
            _LOGGER.info("PUT %s → code %#04x", href, code)

        try:
            await self.hass.async_add_executor_job(_do_put)
        except Exception as e:
            _LOGGER.error("command failed for %s: %s", href, e)
        else:
            await self.async_request_refresh()
