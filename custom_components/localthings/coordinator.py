"""Coordinator for Local Things integration."""
from __future__ import annotations

import logging
import tempfile
import os
from datetime import timedelta
from typing import Any

import cbor2

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo

from samsung_appliance.batch import parse_device0_batch
from samsung_appliance.coap_dtls import DtlsCoapSession
from samsung_appliance.registry.by_type import for_device
from samsung_appliance.registry.discovery import discover, BoundEntity
from samsung_appliance.registry import CAPABILITIES
from samsung_appliance.registry.adapter import flatten, is_active
from samsung_appliance.registry.identity import read_identity, DeviceIdentity

from .const import (
    DOMAIN, CONF_HOST, CONF_PORT, CONF_CERT_PEM, CONF_KEY_PEM,
    ACTIVE_INTERVAL_S, IDLE_INTERVAL_S,
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
            update_interval=timedelta(seconds=IDLE_INTERVAL_S),
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

    # ------------------------------------------------------------------
    # Session management (all blocking — must run in executor)
    # ------------------------------------------------------------------

    def _connect_session(self) -> None:
        host = self._entry.data[CONF_HOST]
        port = self._entry.data[CONF_PORT]
        cert_pem: str = self._entry.data[CONF_CERT_PEM]
        key_pem: str  = self._entry.data[CONF_KEY_PEM]

        cert_f = tempfile.NamedTemporaryFile(suffix='.pem', delete=False, mode='w')
        key_f  = tempfile.NamedTemporaryFile(suffix='.pem', delete=False, mode='w')
        try:
            cert_f.write(cert_pem); cert_f.flush(); cert_f.close()
            key_f.write(key_pem);   key_f.flush();  key_f.close()
            sess = DtlsCoapSession(host, port,
                                   cert_path=cert_f.name,
                                   key_path=key_f.name)
            sess.connect()
            sess.start_reader()
            self._session = sess
            _LOGGER.debug("DTLS connected to %s:%d", host, port)
            try:
                self._identity = read_identity(sess, None)
            except Exception as e:
                _LOGGER.debug("read_identity failed: %s", e)
                self._identity = None
        finally:
            for f in (cert_f.name, key_f.name):
                try:
                    os.unlink(f)
                except OSError:
                    pass

    def _close_session(self) -> None:
        sess = self._session
        self._session = None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass

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
        self._discovered = True
        _LOGGER.info("discovered %d entities (serial=%s)", len(bound), serial)

    # ------------------------------------------------------------------
    # DataUpdateCoordinator hook
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            resources = await self.hass.async_add_executor_job(self._poll_once)
        except Exception as e:
            # One reconnect attempt
            _LOGGER.warning("poll failed, reconnecting: %s", e)
            try:
                await self.hass.async_add_executor_job(self._close_session)
                resources = await self.hass.async_add_executor_job(self._poll_once)
            except Exception as e2:
                raise UpdateFailed(f"poll failed after reconnect: {e2}") from e2

        self._last_resources = resources

        if not self._discovered:
            self._run_discovery(resources)

        # Adjust polling interval dynamically
        active = is_active(self.bound, resources)
        self.update_interval = timedelta(
            seconds=ACTIVE_INTERVAL_S if active else IDLE_INTERVAL_S
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
