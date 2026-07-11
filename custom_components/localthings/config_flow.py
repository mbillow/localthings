"""Config flow for Local Things integration."""
from __future__ import annotations

import datetime
import logging
import re
import socket
import ssl
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DOMAIN,
    CONF_HOST, CONF_PORT,
    CONF_CA_CERT_PEM, CONF_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM,
    PROBE_PORTS,
)

_TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_MULTILINE = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True))

_LOGGER = logging.getLogger(__name__)

_SAMSUNG_CLOUD_HOST = 'connect-v2.samsungiotcloud.com'


class CannotConnect(Exception):
    pass


class InvalidCA(Exception):
    pass


def _fetch_samsung_uuid() -> str:
    """Connect to Samsung's cloud gateway and extract the UUID from its TLS cert.

    Verification is disabled because Samsung's chain contains a self-signed cert.
    We only need to read the UUID from the cert subject, not verify its trust.
    """
    from cryptography import x509 as _x509
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((_SAMSUNG_CLOUD_HOST, 443), timeout=15) as raw:
        with ctx.wrap_socket(raw, server_hostname=_SAMSUNG_CLOUD_HOST) as tls:
            der = tls.getpeercert(binary_form=True)
    cert = _x509.load_der_x509_certificate(der)
    for attr in cert.subject:
        if attr.oid == _x509.oid.NameOID.ORGANIZATIONAL_UNIT_NAME:
            m = re.search(r'uuid:([0-9a-f-]+)', attr.value, re.IGNORECASE)
            if m:
                return m.group(1)
    raise RuntimeError(f"UUID not found in {_SAMSUNG_CLOUD_HOST} certificate subject")


def _mint_leaf_cert(ca_cert_pem: str, ca_key_pem: str, uuid: str) -> tuple[str, str]:
    """Mint a fresh RSA-2048 leaf cert signed by the CA.

    Returns (fullchain_pem, leaf_key_pem) where fullchain_pem is the leaf cert
    followed by the full CA PEM, suitable for use_certificate_chain_file.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

    m = re.search(
        r'(-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----)',
        ca_cert_pem, re.DOTALL,
    )
    if not m:
        raise InvalidCA("No certificate found in CA cert PEM")
    try:
        ca_cert = x509.load_pem_x509_certificate(m.group(1).encode())
        ca_key = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)
    except Exception as exc:
        raise InvalidCA(f"Failed to load CA credentials: {exc}") from exc

    leaf_key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)

    now = datetime.datetime.now(datetime.timezone.utc)
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, 'KR'),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Samsung Electronics'),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, f'uuid:{uuid}'),
            x509.NameAttribute(NameOID.COMMON_NAME, f'urn:uuid:{uuid}'),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=10 * 365))
        .sign(ca_key, hashes.SHA256())
    )

    leaf_cert_pem = leaf_cert.public_bytes(serialization.Encoding.PEM).decode()
    leaf_key_pem = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    # Ensure a newline separates the leaf and CA blocks regardless of
    # whether the user's pasted CA PEM had a trailing newline.
    fullchain_pem = leaf_cert_pem.rstrip('\n') + '\n' + ca_cert_pem
    if not fullchain_pem.endswith('\n'):
        fullchain_pem += '\n'
    return fullchain_pem, leaf_key_pem


def _probe_and_validate(host: str, ca_cert_pem: str, ca_key_pem: str) -> dict:
    """Fetch UUID, mint leaf cert, probe each port. Returns config entry data dict."""
    import cbor2
    from smartthings_local.protocol.dtls_session import DtlsCoapSession
    from .registry.batch import parse_device0_batch
    from .registry.by_type import for_device, for_device_by_model

    _LOGGER.debug("Fetching Samsung cloud UUID from %s", _SAMSUNG_CLOUD_HOST)
    try:
        uuid = _fetch_samsung_uuid()
    except Exception as exc:
        _LOGGER.debug("UUID fetch failed: %s", exc, exc_info=True)
        raise CannotConnect(f"Failed to fetch Samsung UUID: {exc}") from exc
    _LOGGER.debug("Got UUID: %s", uuid)

    _LOGGER.debug("Minting leaf cert for UUID %s", uuid)
    try:
        fullchain_pem, leaf_key_pem = _mint_leaf_cert(ca_cert_pem, ca_key_pem, uuid)
    except InvalidCA:
        _LOGGER.debug("CA credentials invalid", exc_info=True)
        raise
    except Exception as exc:
        _LOGGER.debug("Leaf cert minting failed: %s", exc, exc_info=True)
        raise CannotConnect(f"Failed to mint leaf cert: {exc}") from exc
    _LOGGER.debug("Leaf cert minted successfully")

    last_exc = None
    for port in PROBE_PORTS:
        sess = None
        try:
            sess = DtlsCoapSession(
                host, port,
                cert_pem=fullchain_pem,
                key_pem=leaf_key_pem,
            )
            sess.connect()
            sess.start_reader()
            code, payload = sess.get(['device', '0'], timeout=15.0)
            if code != 0x45 or not payload:
                raise CannotConnect(f"port {port}: unexpected code {code:#04x}")
            body = cbor2.loads(payload)
            resources = (
                parse_device0_batch(body) if isinstance(body, list) else {}
            )
            serial = (
                resources
                .get('/information/vs/0', {})
                .get('x.com.samsung.da.serialNum', '')
            )
            if not serial:
                serial = f"{host}:{port}"
            one_ui_version = (
                resources
                .get('/otninformation/vs/0', {})
                .get('swVersionInfo', {})
                .get('oneUiVersion', '')
            )
            info_resource = resources.get('/information/vs/0', {})
            recognized_registry = (
                for_device(one_ui_version) if one_ui_version else None
            ) or for_device_by_model(
                info_resource.get('x.com.samsung.da.modelNum', ''),
                info_resource.get('x.com.samsung.da.description', ''),
            )
            return {
                "port": port,
                "serial": serial,
                "leaf_cert_pem": fullchain_pem,
                "leaf_key_pem": leaf_key_pem,
                "one_ui_version": one_ui_version,
                "device_type_recognized": recognized_registry is not None,
            }
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


class LocalThingsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._ca_cert_pem: str = ""
        self._ca_key_pem: str = ""
        self._pending_info: dict | None = None

    def _create_entry(self, info: dict) -> FlowResult:
        return self.async_create_entry(
            title=f"Samsung Appliance ({self._host})",
            data={
                CONF_HOST:          self._host,
                CONF_PORT:          info["port"],
                CONF_CA_CERT_PEM:   self._ca_cert_pem,
                CONF_CA_KEY_PEM:    self._ca_key_pem,
                CONF_LEAF_CERT_PEM: info["leaf_cert_pem"],
                CONF_LEAF_KEY_PEM:  info["leaf_key_pem"],
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        existing = self.hass.config_entries.async_entries(DOMAIN)
        has_creds = bool(existing)

        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            if has_creds:
                self._ca_cert_pem = existing[0].data[CONF_CA_CERT_PEM]
                self._ca_key_pem  = existing[0].data[CONF_CA_KEY_PEM]
            else:
                self._ca_cert_pem = user_input[CONF_CA_CERT_PEM].strip()
                self._ca_key_pem  = user_input[CONF_CA_KEY_PEM].strip()

            try:
                info = await self.hass.async_add_executor_job(
                    _probe_and_validate,
                    self._host,
                    self._ca_cert_pem,
                    self._ca_key_pem,
                )
            except InvalidCA:
                errors["base"] = "invalid_ca"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during device probe")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"localthings_{info['serial']}")
                self._abort_if_unique_id_configured()
                if info["device_type_recognized"]:
                    return self._create_entry(info)
                self._pending_info = info
                return await self.async_step_confirm_unknown_type()

        if has_creds:
            schema = vol.Schema({vol.Required(CONF_HOST): _TEXT})
            reuse_note = "CA credentials will be reused from the existing device."
        else:
            schema = vol.Schema({
                vol.Required(CONF_HOST):        _TEXT,
                vol.Required(CONF_CA_CERT_PEM): _MULTILINE,
                vol.Required(CONF_CA_KEY_PEM):  _MULTILINE,
            })
            reuse_note = ""

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"reuse_note": reuse_note},
        )

    async def async_step_confirm_unknown_type(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Shown only when the probe already knows the device type is unrecognized."""
        if user_input is not None:
            return self._create_entry(self._pending_info)

        return self.async_show_form(
            step_id="confirm_unknown_type",
            data_schema=vol.Schema({}),
            description_placeholders={
                "one_ui_version": self._pending_info["one_ui_version"] or "(none reported)",
            },
        )
