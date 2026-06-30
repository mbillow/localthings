"""Config flow for Local Things integration."""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_HOST, CONF_PORT, CONF_CERT_PEM, CONF_KEY_PEM, PROBE_PORTS

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    pass


def _probe_and_validate(host: str, cert_pem: str, key_pem: str) -> dict:
    """Try each known port; return {port, serial} on first success.

    DtlsCoapSession only accepts file paths, so PEM strings are written
    to a temp-file pair and cleaned up in the finally block.
    """
    import cbor2
    from samsung_appliance.coap_dtls import DtlsCoapSession
    from samsung_appliance.batch import parse_device0_batch

    # Write PEM strings to temporary files — DtlsCoapSession requires
    # cert_path / key_path rather than in-memory PEM strings.
    cert_file = tempfile.NamedTemporaryFile(
        mode='w', suffix='.pem', delete=False)
    key_file = tempfile.NamedTemporaryFile(
        mode='w', suffix='.pem', delete=False)
    try:
        cert_file.write(cert_pem)
        cert_file.flush()
        cert_file.close()
        key_file.write(key_pem)
        key_file.flush()
        key_file.close()

        last_exc = None
        for port in PROBE_PORTS:
            sess = None
            try:
                sess = DtlsCoapSession(
                    host, port,
                    cert_path=cert_file.name,
                    key_path=key_file.name,
                )
                sess.connect()
                sess.start_reader()
                code, payload = sess.get(['device', '0'], timeout=15.0)
                if code != 0x45 or not payload:
                    raise CannotConnect(
                        f"port {port}: unexpected code {code:#04x}")
                body = cbor2.loads(payload)
                resources = (
                    parse_device0_batch(body) if isinstance(body, list)
                    else {}
                )
                serial = (
                    resources
                    .get('/information/vs/0', {})
                    .get('x.com.samsung.da.serialNum', '')
                )
                if not serial:
                    serial = (
                        resources
                        .get('/otninformation/vs/0', {})
                        .get('swVersionInfo', {})
                        .get('firmwareVersion', '')
                    )
                if not serial:
                    serial = f"{host}:{port}"
                return {"port": port, "serial": serial}
            except CannotConnect:
                raise
            except Exception as exc:
                last_exc = exc
                _LOGGER.debug("port %d failed: %s", port, exc)
            finally:
                if sess is not None:
                    try:
                        sess.close()
                    except Exception:
                        pass
        raise CannotConnect(f"no port responded on {host}: {last_exc}")
    finally:
        for path in (cert_file.name, key_file.name):
            try:
                os.unlink(path)
            except OSError:
                pass


class LocalThingsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._cert_pem: str = ""
        self._key_pem: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        existing = self.hass.config_entries.async_entries(DOMAIN)
        has_creds = bool(existing)

        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            if has_creds:
                self._cert_pem = existing[0].data[CONF_CERT_PEM]
                self._key_pem = existing[0].data[CONF_KEY_PEM]
            else:
                self._cert_pem = user_input[CONF_CERT_PEM].strip()
                self._key_pem = user_input[CONF_KEY_PEM].strip()

            try:
                info = await self.hass.async_add_executor_job(
                    _probe_and_validate,
                    self._host,
                    self._cert_pem,
                    self._key_pem,
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during device probe")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"localthings_{info['serial']}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Samsung Appliance ({self._host})",
                    data={
                        CONF_HOST:     self._host,
                        CONF_PORT:     info["port"],
                        CONF_CERT_PEM: self._cert_pem,
                        CONF_KEY_PEM:  self._key_pem,
                    },
                )

        if has_creds:
            schema = vol.Schema({vol.Required(CONF_HOST): str})
            reuse_note = "Certificate and key will be reused from the existing device."
        else:
            schema = vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_CERT_PEM): str,
                vol.Required(CONF_KEY_PEM): str,
            })
            reuse_note = ""

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"reuse_note": reuse_note},
        )
