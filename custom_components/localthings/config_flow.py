"""Config flow for Local Things integration."""
from __future__ import annotations

import datetime
import json
import logging
import re
import selectors
import socket
import ssl
import time
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    ObjectSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DOMAIN,
    CONF_HOST, CONF_PORT,
    CONF_CA_CERT_PEM, CONF_CA_KEY_PEM,
    CONF_LEAF_CERT_PEM, CONF_LEAF_KEY_PEM,
    CONF_BYPASS_REMOTE_CONTROL,
    PROBE_PORT_RANGE, PREFERRED_PROBE_PORTS, LIVENESS_PROBE_TIMEOUT_S,
    PROBE_GET_TIMEOUT_S,
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


def _order_candidates(ports: list[int]) -> list[int]:
    """Order live ports so the historically known DTLS ports are tried first."""
    preferred = [p for p in PREFERRED_PROBE_PORTS if p in ports]
    rest = sorted(p for p in ports if p not in PREFERRED_PROBE_PORTS)
    return preferred + rest


def _find_live_ports(host: str, ports: list[int], timeout: float) -> list[int]:
    """Fast UDP liveness sweep to narrow the range before the DTLS handshake.

    UDP is connectionless, but a *connected* UDP socket surfaces the ICMP
    port-unreachable that a closed port returns as ECONNREFUSED on its next
    recv. So we send one probe datagram per port and watch for that error:

      * ECONNREFUSED       -> port is closed (device actively rejected it)
      * silence / any data -> port may be live (open|filtered); a candidate

    This is the in-process equivalent of ``nmap -sU``: it lets us take a
    nine-port range down to the one or two ports actually worth a full DTLS
    handshake + /device/0 GET, and bounds the total wait to ``timeout``
    instead of stalling on every dead port when a firewall swallows the ICMP
    replies.
    """
    sockets: dict[int, socket.socket] = {}
    sel = selectors.DefaultSelector()
    # A single byte is enough to provoke an ICMP port-unreach from a closed
    # port; a real DTLS ClientHello is unnecessary just to test for life.
    probe = b"\x00"
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                sock.connect((host, port))
                sock.send(probe)
            except OSError:
                sock.close()
                continue
            sockets[port] = sock
            sel.register(sock, selectors.EVENT_READ, port)

        # Ports drop out of the selector as they refuse; whatever is still
        # registered when the deadline passes is silent-but-live (a candidate).
        deadline = time.monotonic() + timeout
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for key, _ in sel.select(timeout=remaining):
                try:
                    # Data back means live; ECONNREFUSED (or any other socket
                    # error) means the port is closed/unusable — rule it out.
                    key.fileobj.recv(1)
                except OSError:
                    sel.unregister(key.fileobj)
        live = [key.data for key in sel.get_map().values()]
    finally:
        sel.close()
        for sock in sockets.values():
            try:
                sock.close()
            except OSError:
                pass

    return _order_candidates(live)


def _is_placeholder_serial(serial: str) -> bool:
    """True for a non-empty serialNum that isn't actually a real identity.

    The ARTIK051_DONGLE_REF firmware family reports the literal string
    'Nothing(SVC)' for every unit -- non-empty, so the plain `if not
    serial` check here (and the equivalent one in coordinator.py's
    `_run_discovery`) doesn't catch it, and two such units get the same
    config-entry unique_id / entity unique_ids and collide (issue #83).
    """
    return serial.strip().lower().startswith('nothing')


def _probe_and_validate(host: str, ca_cert_pem: str, ca_key_pem: str) -> dict:
    """Fetch UUID, mint leaf cert, probe each port. Returns config entry data dict."""
    import cbor2
    from smartthings_local.protocol.dtls_session import DtlsCoapSession
    from .registry.batch import parse_device0_batch
    from .registry.by_type import (
        for_device, for_device_by_model, for_device_by_resources,
    )

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

    candidates = _find_live_ports(
        host, PROBE_PORT_RANGE, LIVENESS_PROBE_TIMEOUT_S
    )
    if not candidates:
        # Every port in the range actively refused: there is no DTLS/CoAP
        # listener on this host, so a handshake can't succeed. Fail now with a
        # clear message instead of retrying doomed ports.
        raise CannotConnect(
            f"no live DTLS port found on {host} "
            f"in {PROBE_PORT_RANGE[0]}-{PROBE_PORT_RANGE[-1]}"
        )
    _LOGGER.debug("Live DTLS port candidates on %s: %s", host, candidates)

    last_exc = None
    for port in candidates:
        sess = None
        try:
            sess = DtlsCoapSession(
                host, port,
                cert_pem=fullchain_pem,
                key_pem=leaf_key_pem,
            )
            sess.connect()
            sess.start_reader()
            code, payload = sess.get(['device', '0'], timeout=PROBE_GET_TIMEOUT_S)
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
            if not serial or _is_placeholder_serial(serial):
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
            ) or for_device_by_resources(resources)
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> LocalThingsOptionsFlow:
        return LocalThingsOptionsFlow()

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
            step_id = "user_reuse"
        else:
            schema = vol.Schema({
                vol.Required(CONF_HOST):        _TEXT,
                vol.Required(CONF_CA_CERT_PEM): _MULTILINE,
                vol.Required(CONF_CA_KEY_PEM):  _MULTILINE,
            })
            step_id = "user"

        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
        )

    async def async_step_user_reuse(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the localized host-only form for additional appliances."""
        return await self.async_step_user(user_input)

    async def async_step_confirm_unknown_type(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Shown only when the probe already knows the device type is unrecognized."""
        if user_input is not None:
            return self._create_entry(self._pending_info)

        if not self._pending_info["one_ui_version"]:
            return await self.async_step_confirm_unknown_type_no_version()

        return self.async_show_form(
            step_id="confirm_unknown_type",
            data_schema=vol.Schema({}),
            description_placeholders={
                "one_ui_version": self._pending_info["one_ui_version"],
            },
        )

    async def async_step_confirm_unknown_type_no_version(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm an unknown appliance that did not report oneUiVersion."""
        if user_input is not None:
            return self._create_entry(self._pending_info)
        return self.async_show_form(
            step_id="confirm_unknown_type_no_version",
            data_schema=vol.Schema({}),
        )


class LocalThingsOptionsFlow(config_entries.OptionsFlow):
    """Per-device options: the remote-control-off write-block override
    (issue #54) plus a debug panel for writing an arbitrary body to an
    arbitrary resource href, so a user can pin down device-specific write
    behavior without waiting on a new release.

    The remote-control override exists because most devices reject writes
    outright while remote control is off and a clear error beats a silent
    device-side rejection -- but not every model actually enforces that,
    so this lets a user who's confirmed their device accepts writes anyway
    turn the block off for just that device rather than it being
    hardcoded on for everyone. The debug panel goes further: it bypasses
    that block (and every write_fn/validate_fn) entirely, sending exactly
    the body the user types to whatever href they pick.
    """

    def __init__(self) -> None:
        self._debug_href: str = ""
        self._debug_result: tuple[int, dict] | None = None

    def _coordinator(self):
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "debug_write"],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_BYPASS_REMOTE_CONTROL,
                    default=self.config_entry.options.get(
                        CONF_BYPASS_REMOTE_CONTROL, False
                    ),
                ): bool,
            }),
        )

    async def async_step_debug_write(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")

        if user_input is not None:
            self._debug_href = user_input["href"]
            return await self.async_step_debug_edit()

        hrefs = sorted(coord.last_resources.keys())
        return self.async_show_form(
            step_id="debug_write",
            data_schema=vol.Schema({
                vol.Required("href"): SelectSelector(SelectSelectorConfig(
                    options=hrefs,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )),
            }),
        )

    def _show_debug_edit_form(
        self, href: str, current: dict, errors: dict[str, str], payload,
    ) -> FlowResult:
        return self.async_show_form(
            step_id="debug_edit",
            data_schema=vol.Schema({
                vol.Required(
                    "payload", default=(payload if payload is not None else {}),
                ): ObjectSelector(),
            }),
            errors=errors,
            description_placeholders={
                "href": href,
                "current_value": (
                    json.dumps(current, indent=2, ensure_ascii=False)
                    if current else "{}"
                ),
            },
        )

    async def async_step_debug_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        coord = self._coordinator()
        if coord is None:
            return self.async_abort(reason="not_loaded")

        href = self._debug_href
        current = coord.resource(href)

        if user_input is not None:
            payload = user_input.get("payload") or {}
            if not isinstance(payload, dict) or not payload:
                return self._show_debug_edit_form(
                    href, current, {"payload": "empty_payload"}, payload
                )
            try:
                code, new_rep = await coord.async_raw_write(href, payload)
            except Exception:  # noqa: BLE001 - surfaced to the user below
                _LOGGER.exception("debug raw write failed for %s", href)
                return self._show_debug_edit_form(
                    href, current, {"base": "write_failed"}, payload
                )
            self._debug_result = (code, new_rep)
            return await self.async_step_debug_result()

        return self._show_debug_edit_form(href, current, {}, None)

    async def async_step_debug_result(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        code, new_rep = self._debug_result or (0, {})
        return self.async_show_menu(
            step_id="debug_result",
            menu_options=["debug_write", "finish"],
            description_placeholders={
                "code": f"{code >> 5}.{code & 0x1f:02d} ({code:#04x})",
                "new_value": (
                    json.dumps(new_rep, indent=2, ensure_ascii=False)
                    if new_rep else "{}"
                ),
            },
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        # Close the flow without altering saved options.
        return self.async_create_entry(data=dict(self.config_entry.options))
