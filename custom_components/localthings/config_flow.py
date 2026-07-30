"""Config flow for Local Things integration."""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import selectors
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any, Callable

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
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
    RACE_OVERALL_S,
)

_TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_MULTILINE = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True))

_LOGGER = logging.getLogger(__name__)

_SAMSUNG_CLOUD_HOST = 'connect-v2.samsungiotcloud.com'


class CannotConnect(Exception):
    pass


class InvalidCA(Exception):
    pass


class _CertRejected(Exception):
    """Raised by a probe worker when the device rejects the client cert
    (DTLS alert: handshake_failure / bad_certificate / unknown_ca / ...).
    Distinguished from a port TimeoutError so the probe can decide whether a
    full-cert-rejection warrants a UUID-rotation re-mint fallback."""


# DTLS alert phrases that signal the device rejected the client cert. The
# first four are the spec/design's cert-reject alerts. The extra two widen
# the net: `access denied` (alert 49) is the device's ACL rejecting the peer
# UUID — a genuine cert-rejection signal in OCF; `decrypt error` (alert 51)
# is broader but included so an all-ports crypto failure still triggers the
# re-mint attempt. The fallback is bounded: it only fires when ALL
# candidates match, so a spurious match on one port is harmless.
_CERT_ALERT_MARKERS = (
    'handshake failure', 'bad certificate', 'unknown ca',
    'certificate unknown', 'access denied', 'decrypt error',
)


def _is_cert_alert(exc: Exception) -> bool:
    """True iff a DtlsCoapSession.connect() failure looks like a DTLS cert-reject
    alert. DtlsCoapSession wraps SSL.Error as ConnectionError("DTLS handshake
    error: <SSL.Error str>"); we match on the alert phrase in that string.
    Conservative: anything unrecognized is treated as a generic handshake error
    (NOT a cert reject), so the UUID-rotation fallback never fires on a guess."""
    msg = str(exc).lower()
    return any(m in msg for m in _CERT_ALERT_MARKERS)


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

    # The sweep's ICMP-based verdict isn't reliable on every network path --
    # issue #192 captured a segregated-VLAN device where it called three
    # ports live that a concurrent nmap scan showed as closed, while the
    # port nmap found genuinely open|filtered (49154, one of our historically
    # confirmed ports) never showed up as live at all. Rather than trust a
    # wrong "not live" verdict on a port we already have strong prior
    # evidence for, always give the historically-confirmed ports a real
    # handshake attempt too. Bounded cost: at most len(PREFERRED_PROBE_PORTS)
    # extra handshakes, only when the sweep disagrees with the prior.
    rescued = [p for p in PREFERRED_PROBE_PORTS if p in ports and p not in live]
    return _order_candidates(live + rescued)


def _is_placeholder_serial(serial: str) -> bool:
    """True for a non-empty serialNum that isn't actually a real identity.

    The ARTIK051_DONGLE_REF firmware family reports the literal string
    'Nothing(SVC)' for every unit -- non-empty, so the plain `if not
    serial` check here (and the equivalent one in coordinator.py's
    `_run_discovery`) doesn't catch it, and two such units get the same
    config-entry unique_id / entity unique_ids and collide (issue #83).
    """
    return serial.strip().lower().startswith('nothing')


def _race_handshake(
    host: str, candidates: list[int], worker: Callable[[int], dict],
) -> tuple[dict | None, bool, Exception | None]:
    """Race `worker(port)` across `candidates` in parallel threads.

    `worker(port)` must return an info dict on success or raise:
      * _CertRejected  — device rejected the client cert
      * TimeoutError    — port didn't answer the DTLS handshake
      * Exception       — any other failure (SSL error, GET code != 2.05, ...)

    Returns (info, False, None) as soon as the first worker returns a valid
    info dict. Losing workers are NOT waited for: shutdown(wait=False) leaves
    them running as daemon threads (ThreadPoolExecutor workers are daemons
    on Python 3.9+), and each worker's `finally: sess.close()` cleans its
    socket when it eventually times out / errors.
    Returns (None, cert_rejected, last_exc) if no worker succeeds, where
    cert_rejected is True iff every candidate raised _CertRejected — the
    signal the caller uses to decide whether to re-mint the leaf cert once —
    and last_exc is the most recent exception seen (timeout, cert-reject, or
    generic) so the caller's CannotConnect message can carry a per-port hint
    about why the race failed (e.g. the DTLS timeout when all ports went
    silent).
    """
    cert_error_count = 0
    last_exc: Exception | None = None
    ex = ThreadPoolExecutor(max_workers=max(1, len(candidates)))
    try:
        futs = [ex.submit(worker, p) for p in candidates]
        try:
            for fut in as_completed(futs, timeout=RACE_OVERALL_S):
                try:
                    info = fut.result()
                except _CertRejected as e:
                    cert_error_count += 1
                    last_exc = e
                    _LOGGER.debug("race: port rejected cert: %s", e)
                except Exception as e:  # noqa: BLE001 — classify by type below
                    last_exc = e
                    _LOGGER.debug("race: port failed: %s", e)
                else:
                    return info, False, None
        except FuturesTimeoutError as e:
            last_exc = e
            _LOGGER.debug("race: no winner within %.1fs", RACE_OVERALL_S)
    finally:
        ex.shutdown(wait=False)
    cert_rejected = cert_error_count == len(candidates) and cert_error_count > 0
    return None, cert_rejected, last_exc


def _persist_refreshed_leaf(
    hass: HomeAssistant, entry: config_entries.ConfigEntry, fullchain_pem: str, key_pem: str,
) -> None:
    """Update an existing config entry's leaf cert/key after a UUID-rotation
    re-mint, so subsequent device adds reuse the fresh leaf.

    Called from the executor thread (_probe_and_validate runs there), so we
    schedule the async update on the HA loop and block briefly for it to
    complete. Best-effort: a failure to persist must not abort the in-progress
    add (the info dict already carries the fresh leaf for THIS entry).
    """
    async def _update():
        await hass.config_entries.async_update_entry(
            entry,
            data={**entry.data,
                  CONF_LEAF_CERT_PEM: fullchain_pem,
                  CONF_LEAF_KEY_PEM: key_pem},
        )
    try:
        fut = asyncio.run_coroutine_threadsafe(_update(), hass.loop)
        fut.result(timeout=5.0)
    except Exception as e:  # noqa: BLE001 — best-effort
        _LOGGER.warning("Failed to persist refreshed leaf cert: %s", e)


def _probe_and_validate(
    host: str,
    ca_cert_pem: str,
    ca_key_pem: str,
    cached_leaf: tuple[str, str] | None = None,
    hass: Any = None,
    entry: Any = None,
) -> dict:
    """Fetch UUID (or reuse cached_leaf), mint leaf cert if needed, race ports.

    Returns config entry data dict. ``cached_leaf`` is an optional
    ``(fullchain_pem, key_pem)`` tuple reused from a prior config entry — when
    provided, the Samsung-cloud UUID fetch and leaf mint are skipped and the
    cached leaf is raced directly. On all-cert-rejected with a cached leaf
    present, a one-shot fresh-mint fallback fires: re-fetch UUID, re-mint,
    race again. When that fallback succeeds and ``hass``+``entry`` are
    provided, the refreshed leaf is persisted to the existing entry via
    ``_persist_refreshed_leaf`` so subsequent device adds reuse it.
    """
    import cbor2
    from smartthings_local.protocol.dtls_session import DtlsCoapSession
    from .registry.batch import parse_device0_batch
    from .registry.by_type import resolve as resolve_registry

    if cached_leaf is not None:
        fullchain_pem, leaf_key_pem = cached_leaf
        _LOGGER.debug("Reusing cached leaf cert (UUID fetch + mint skipped)")
    else:
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
    # No early "every port refused" fast-fail here: _find_live_ports always
    # rescues PREFERRED_PROBE_PORTS (issue #192), so candidates is never
    # empty as long as that table is non-empty and within PROBE_PORT_RANGE --
    # both true today, which made this branch permanently unreachable. A
    # genuinely dead host now fails via the handshake loop's own error below,
    # which carries the actual per-port timeout/refusal reason instead of a
    # generic "no live port found" message.
    _LOGGER.debug("Live DTLS port candidates on %s: %s", host, candidates)

    def _make_worker(
        cert_pem: str, key_pem: str, session_factory: Callable[..., Any] = DtlsCoapSession,
    ) -> Callable[[int], dict]:
        """Nested per-port worker closure. Captures `host` and the
        function-level imports (cbor2, parse_device0_batch, resolve_registry).
        session_factory defaults to DtlsCoapSession (function-level import,
        re-resolved each _probe_and_validate call so monkeypatches take)."""
        def worker(port):
            sess = session_factory(host, port, cert_pem=cert_pem, key_pem=key_pem)
            try:
                try:
                    sess.connect()
                except TimeoutError:
                    raise
                except Exception as e:
                    if _is_cert_alert(e):
                        raise _CertRejected(str(e)) from e
                    raise
                sess.start_reader()
                code, payload = sess.get(['device', '0'], timeout=PROBE_GET_TIMEOUT_S)
                if code != 0x45 or not payload:
                    raise CannotConnect(f"port {port}: unexpected code {code:#04x}")
                body = cbor2.loads(payload)
                resources = (
                    parse_device0_batch(body) if isinstance(body, list) else {}
                )
                serial = (
                    resources.get('/information/vs/0', {})
                    .get('x.com.samsung.da.serialNum', '')
                )
                if not serial or _is_placeholder_serial(serial):
                    serial = f"{host}:{port}"
                recognized_registry = resolve_registry(resources)
                return {
                    "port": port,
                    "serial": serial,
                    "leaf_cert_pem": cert_pem,
                    "leaf_key_pem": key_pem,
                    "device_type_recognized": recognized_registry is not None,
                }
            finally:
                try:
                    sess.close()
                except Exception:
                    pass
        return worker

    worker = _make_worker(fullchain_pem, leaf_key_pem)
    info, cert_rejected, last_exc = _race_handshake(host, candidates, worker)

    if info is None and cert_rejected and cached_leaf is not None:
        # All candidates rejected the cached leaf cert → UUID likely rotated.
        # Re-fetch UUID + re-mint once, then race again with the fresh leaf.
        _LOGGER.info("Cached leaf rejected by all ports; re-minting (UUID rotation?)")
        try:
            uuid = _fetch_samsung_uuid()
            fullchain_pem, leaf_key_pem = _mint_leaf_cert(ca_cert_pem, ca_key_pem, uuid)
        except InvalidCA:
            raise
        except Exception as exc:
            raise CannotConnect(f"re-mint after cert rejection failed: {exc}") from exc
        info, _, last_exc = _race_handshake(
            host, candidates, _make_worker(fullchain_pem, leaf_key_pem),
        )
        if info is not None and hass is not None and entry is not None:
            _persist_refreshed_leaf(hass, entry, fullchain_pem, leaf_key_pem)

    if info is None:
        base = f"no port responded on {host}"
        if last_exc is not None:
            base = f"{base}: {last_exc}"
        raise CannotConnect(
            base + (" (cert rejected)" if cert_rejected else "")
        )
    return info


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

            cached_leaf = None
            entry_ref = None
            if has_creds:
                cached_leaf = (
                    existing[0].data[CONF_LEAF_CERT_PEM],
                    existing[0].data[CONF_LEAF_KEY_PEM],
                )
                entry_ref = existing[0]

            try:
                info = await self.hass.async_add_executor_job(
                    _probe_and_validate,
                    self._host,
                    self._ca_cert_pem,
                    self._ca_key_pem,
                    cached_leaf,
                    self.hass,
                    entry_ref,
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
        return self.async_show_form(
            step_id="confirm_unknown_type",
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
