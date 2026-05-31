"""Push-mode bridge: OCF CoAP-DTLS Observe → MQTT.

  Appliance ──CoAP OBSERVE notifications──►  PushBridge
                                                  │
                                                  ▼
                                              MQTT broker
                                                  │
                                                  ▼
                                          Home Assistant

State changes push from the appliance over a sustained DTLS session.
The bridge updates an in-memory link dict, recomputes flat sensors via
the appliance descriptor, and publishes to MQTT ONLY when the flat-
sensor dict actually changes.

The bridge is appliance-class-agnostic — it delegates every
appliance-specific decision to an ApplianceDescriptor.

Multiple PushBridges run concurrently in a single process — see
main.py. They share one MQTT client; each owns one DTLS session.
"""
import json
import os
import threading
import time

import cbor2

from .appliances.base import ApplianceDescriptor
from .coap_dtls import DtlsCoapSession, fmt_code
from .config import ApplianceConfig, SharedConfig
from .logger import bridge_logger, logger as module_logger
from .sensors import index_links


# Re-enable with `DEBUG_BRIDGE=1` env var. When on, the bridge dumps:
#   - every received CoAP frame (in coap_dtls.py)
#   - the full /device/0 link tree at seed time
#   - /oic/res directory
#   - REP changes for /operational/state, /oven, /power, /mode-options
# Designed for reverse-engineering new oven/dryer behaviour next
# session — leave at 0 in production.
DEBUG_BRIDGE = os.environ.get('DEBUG_BRIDGE') == '1'


def _href_to_segs(href: str) -> list[str]:
    """`/mode/vs/0` → `['mode', 'vs', '0']`. Used to translate an
    OBSERVE-notification href back into the path-segs the Block2 GET
    needs."""
    return [s for s in href.split('/') if s]


# Samsung's `/information/vs/0` resource carries a unique serial number.
# Verified on both dryer (DV5000T) and oven (NV7000BS); we use the
# value to tag per-bridge log lines once the seed completes.
SERIAL_PATH = '/information/vs/0'
SERIAL_FIELD = 'x.com.samsung.da.serialNum'


class PushBridge:
    """Single sustained DTLS-CoAP session to one appliance.

    Reconnects with exponential backoff on session errors. Publishes
    availability=offline when the appliance is unreachable so HA marks
    entities unavailable instead of trusting stale state."""

    def __init__(self,
                 shared: SharedConfig,
                 app: ApplianceConfig,
                 descriptor: ApplianceDescriptor,
                 mqtt_client):
        self.shared = shared
        self.app = app
        self.descriptor = descriptor
        self.mqtt = mqtt_client

        # Bridge-scoped logger; retagged with serial after first seed.
        self.log = bridge_logger(app.klass)
        self._serial: str | None = None

        # Resolve port (descriptor default if unset in env).
        self.port = app.ocf_port or descriptor.default_observe_port

        self.session: DtlsCoapSession | None = None
        self.links: dict[str, dict] = {}     # href → rep
        self.descriptor_state: dict = {}     # descriptor scratch space

        self.last_state_pub = None
        self.last_remote_pub = None
        self.last_cycle_pub = None
        self.stop = threading.Event()
        self.started_ts = time.time()
        self.session_started_ts = None
        self.last_change_ts = None
        self.last_seed_ts = None
        self.notif_count = 0
        self.connect_count = 0
        self.error_count = 0
        self._publish_gate = False

        # Per-href fetchback generation counter. Every new schedule
        # bumps the gen; a fetchback aborts on wake (and again after
        # its GET completes) if its captured gen is no longer the
        # latest. This coalesces bursts: rapid lamp toggles or slider
        # drags result in many scheduled fetchbacks but only the
        # latest one actually publishes. Lock guards the dict mutation
        # and the gen comparison.
        self._fetch_gen: dict[str, int] = {}
        self._fetch_lock = threading.Lock()

        p = app.topic_prefix
        self.state_topic    = f"{p}/state"
        self.avail_topic    = f"{p}/availability"
        self.remote_topic   = f"{p}/remote_available"
        self.cycle_topic    = f"{p}/cycle_active"
        self.health_topic   = f"{p}/bridge/health"
        self.cmd_handlers   = descriptor.command_handlers()
        self.cmd_topic_prefix = f"{p}/cmd/"

        # Pre-built HA discovery payloads. Republished on every MQTT
        # (re)connect by main.py.
        self.discovery_payloads = descriptor.build_discovery(
            app.topic_prefix, shared.HA_DISCOVERY_PREFIX, app.device_name)

    # ---- DTLS session helpers ---------------------------------------

    def _on_notification(self, href, payload_bytes):
        """Invoked by the DTLS reader thread for OBSERVE notifications.

        Resources larger than one CoAP block (notably the oven's
        `/mode/vs/0` at ~9KB) arrive truncated: Samsung sends only
        block 0 with Block2.M=1 and expects the client to fetch the
        rest via Block2 GET. We use cbor decode failure as the
        robust "this notification is partial" signal, then spawn a
        worker thread to fetch the full resource."""
        if not payload_bytes:
            # Empty payload — almost certainly a Block2 announcement.
            self._schedule_fetchback(href)
            return
        try:
            rep = cbor2.loads(payload_bytes)
        except Exception:
            self._schedule_fetchback(href)
            return
        if not isinstance(rep, dict):
            return
        self._apply_rep(href, rep)

    def _apply_rep(self, href, rep):
        """Update self.links + fire descriptor hooks + maybe publish.
        Shared between the OBSERVE path and the Block2 fetch-back path."""
        if DEBUG_BRIDGE:
            # /mode/vs/0 carries a huge modeSpec JSON we don't want to
            # log; surface just modes + options. Small resources get
            # full-rep dumps.
            if href == '/mode/vs/0' and isinstance(rep, dict):
                self.log.info("mode modes=%r options=%r",
                              rep.get('x.com.samsung.da.modes'),
                              rep.get('x.com.samsung.da.options'))
            elif href in ('/operational/state/vs/0', '/oven/vs/0',
                          '/power/vs/0'):
                self.log.info("REP %s = %r", href, rep)
        self.links[href] = rep
        hook = self.descriptor.on_observation
        if hook is not None:
            try:
                hook(self.descriptor_state, href, rep)
            except Exception as e:
                self.log.warning("on_observation %s: %s", href, e)
        self.notif_count += 1
        self.last_change_ts = time.time()
        self.maybe_publish_state()

    def _apply_optimistic(self, href, body):
        """Optimistically merge a just-POSTed body into the link dict
        and republish state. Samsung accepts (2.04) writes whose
        bodies are field-replacements — we mirror that semantics here:
        each top-level key in `body` overwrites the corresponding key
        in the existing rep. The fetchback that follows republishes
        the device's real state, which corrects any field where the
        write was silently coerced or rejected."""
        if not isinstance(body, dict):
            return
        rep = dict(self.links.get(href) or {})
        rep.update(body)
        self._apply_rep(href, rep)

    def _schedule_fetchback(self, href, delay_s: float = 0.0):
        """Spawn a worker thread to fetch the full payload of `href`
        via Block2 GET. Each schedule bumps a per-href generation
        counter — if a newer fetchback is scheduled before this one
        fires, this one aborts (so rapid commands coalesce into a
        single verification read of the FINAL state)."""
        with self._fetch_lock:
            gen = self._fetch_gen.get(href, 0) + 1
            self._fetch_gen[href] = gen
        threading.Thread(
            target=self._fetch_back,
            args=(href, delay_s, gen),
            daemon=True,
            name=f'fetch{href}',
        ).start()

    def _fetch_back(self, href, delay_s: float, gen: int):
        if delay_s > 0:
            # Allow the device's read-side to propagate a recent
            # write. The oven needs ~1s after a /mode/vs/0 POST;
            # the dryer is faster but the delay is harmless there.
            if self.stop.wait(delay_s):
                return
        # Has a newer fetchback been scheduled during our delay?
        # If so, abort — our read would publish stale state relative
        # to the user's most recent intent.
        with self._fetch_lock:
            if self._fetch_gen.get(href) != gen:
                return
        sess = self.session
        if sess is None:
            return
        segs = _href_to_segs(href)
        try:
            code, payload = sess.get(segs, timeout=15.0)
        except Exception as e:
            self.log.warning("fetchback %s: %s", href, e)
            return
        # Re-check generation after the GET — a new write may have
        # come in during the Block2 round-trip, in which case our
        # payload is also superseded.
        with self._fetch_lock:
            if self._fetch_gen.get(href) != gen:
                return
        if code != 0x45:
            self.log.warning("fetchback %s: %s",
                             href, fmt_code(code))
            return
        try:
            rep = cbor2.loads(payload) if payload else {}
        except Exception as e:
            self.log.warning("fetchback %s cbor: %s", href, e)
            return
        if not isinstance(rep, dict):
            return
        self._apply_rep(href, rep)

    def _retag_logger_with_serial(self):
        """Look up the appliance's serial in the seeded link dict and
        retarget self.log to a serial-tagged child. Idempotent."""
        if self._serial is not None:
            return
        info = self.links.get(SERIAL_PATH) or {}
        serial = info.get(SERIAL_FIELD)
        if not serial:
            return
        self._serial = serial
        self.log = bridge_logger(self.app.klass, serial)
        self.log.info("identified — serial=%s", serial)

    # ---- session lifecycle ------------------------------------------

    def session_once(self):
        """Run one DTLS session end-to-end. Raises on error; the outer
        run_forever wraps this with reconnect/backoff."""
        sess = DtlsCoapSession(
            self.app.ip, self.port,
            cert_path=self.shared.CERT_PATH,
            key_path=self.shared.KEY_PATH,
            on_notification=self._on_notification,
        )
        sess.connect()
        self.session = sess
        self.session_started_ts = time.time()
        self.connect_count += 1
        self.descriptor_state = {}
        self._publish_gate = False

        self.log.info("DTLS connected — subscribing %d paths",
                      len(self.descriptor.observe_paths))

        sess.start_reader()

        # When the bridge is asked to stop, close the session — which
        # fires the OBSERVE-dereg sequence and tears DTLS down. Without
        # this, session_once would block forever in sess.join() because
        # the reader only exits on stop/socket-death.
        session_ended = threading.Event()

        def _stop_watcher():
            while not session_ended.is_set():
                if self.stop.wait(1.0):
                    try:
                        sess.close()
                    except Exception as e:
                        self.log.warning("stop close: %s", e)
                    return

        threading.Thread(target=_stop_watcher, daemon=True,
                         name=f'{self.app.klass}-stopw').start()

        try:
            self._run_session_inner(sess)
        finally:
            # Always release the watcher so it doesn't sit pinned on
            # self.stop forever after the session ends.
            session_ended.set()

    def _run_session_inner(self, sess):
        """Body of session_once after start_reader() — split out so
        session_once's try/finally cleanly bounds the stop-watcher
        thread's lifetime."""
        for path in self.descriptor.observe_paths:
            sess.subscribe(path)
            time.sleep(0.05)

        code, pl = sess.get(self.descriptor.seed_path, timeout=15.0)
        if code != 0x45:
            raise RuntimeError(
                f"/{'/'.join(self.descriptor.seed_path)} -> {fmt_code(code)}")
        try:
            body = cbor2.loads(pl)
        except Exception as e:
            raise RuntimeError(
                f"/{'/'.join(self.descriptor.seed_path)} cbor decode: {e}"
            ) from e
        for href, rep in index_links(body).items():
            self.links.setdefault(href, rep)

        # Once the seed is in, we know the appliance's serial — retag
        # the logger so the remaining log lines this session emits are
        # serial-tagged.
        self._retag_logger_with_serial()

        if DEBUG_BRIDGE:
            # Dump every link's rep (skipping the huge modeSpec on
            # /mode/vs/0) + the /oic/res directory. Useful for finding
            # new writable resources next session.
            for href, rep in sorted(self.links.items()):
                if href == '/mode/vs/0':
                    short = {k: v for k, v in rep.items()
                             if k not in (
                                 'x.com.samsung.da.modeSpec',
                                 'x.com.samsung.da.supportedModes',
                             )}
                    self.log.info("LINK %s = %r", href, short)
                else:
                    self.log.info("LINK %s = %r", href, rep)
            try:
                code, pl = sess.get(['oic', 'res'], timeout=10.0)
                if code == 0x45 and pl:
                    self.log.info("OIC_RES = %r", cbor2.loads(pl))
                else:
                    self.log.info("oic/res → %s", fmt_code(code))
            except Exception as e:
                self.log.warning("oic/res get: %s", e)


        hook = self.descriptor.on_observation
        if hook is not None:
            for href, rep in self.links.items():
                try:
                    hook(self.descriptor_state, href, rep)
                except Exception as e:
                    self.log.warning("seed on_observation %s: %s", href, e)
        self.last_seed_ts = time.time()

        self._publish_gate = True
        self.maybe_publish_state(force=True)
        self.set_availability(True)
        self.log.info("seeded → %d links; sensors live", len(self.links))

        sess.join()

    def heartbeat(self):
        sess = self.session
        if sess is None:
            return
        try:
            code, pl = sess.get(self.descriptor.seed_path, timeout=15.0)
        except Exception as e:
            self.log.warning("heartbeat seed: %s", e)
            return
        if code != 0x45:
            self.log.warning("heartbeat seed: %s", fmt_code(code))
            return
        try:
            body = cbor2.loads(pl)
        except Exception as e:
            self.log.warning("heartbeat seed cbor: %s", e)
            return
        # Refresh ALL resources, not just non-observed ones. We used
        # to skip observed resources on the assumption OBSERVE kept
        # them fresh, but the oven doesn't reliably push OBSERVE on
        # /mode/vs/0 option changes (timer / lamp / sound), so we'd
        # be stuck with stale values until the next user-driven POST
        # triggered a fetchback. Refreshing everything bounds HA's
        # divergence to HEARTBEAT_INTERVAL_S in the worst case.
        hook = self.descriptor.on_observation
        for href, rep in index_links(body).items():
            self.links[href] = rep
            if hook is not None:
                try:
                    hook(self.descriptor_state, href, rep)
                except Exception as e:
                    self.log.warning("heartbeat hook %s: %s", href, e)
        self.last_seed_ts = time.time()
        self.maybe_publish_state()

    # ---- MQTT publishing --------------------------------------------

    def maybe_publish_state(self, force=False):
        if not force and not self._publish_gate:
            return
        sensors = self.descriptor.flatten(self.links)
        project = self.descriptor.project
        if project is not None:
            sensors = project(self.descriptor_state, sensors)
        if not force and sensors == self.last_state_pub:
            return
        self.last_state_pub = sensors
        self.mqtt.publish(self.state_topic,
                          json.dumps(sensors).encode(),
                          qos=1, retain=True)
        field = self.descriptor.remote_available_field
        if field is not None:
            self.publish_remote_available(sensors.get(field))
        cycle_field = self.descriptor.cycle_active_field
        if cycle_field is not None:
            self.publish_cycle_active(sensors.get(cycle_field))
        if not force:
            log_fn = self.descriptor.log_state_change
            extra = log_fn(sensors) if log_fn is not None else ''
            self.log.info("state changed (%s notif#%d)",
                          extra or 'descriptor-no-log', self.notif_count)

    def publish_remote_available(self, remote_on, force=False):
        value = 'online' if remote_on else 'offline'
        if not force and value == self.last_remote_pub:
            return
        self.last_remote_pub = value
        try:
            self.mqtt.publish(self.remote_topic, value, qos=1, retain=True)
            self.log.info("remote_available → %s", value)
        except Exception as e:
            self.log.warning("remote_available publish: %s", e)

    def publish_cycle_active(self, cycle_on, force=False):
        value = 'online' if cycle_on else 'offline'
        if not force and value == self.last_cycle_pub:
            return
        self.last_cycle_pub = value
        try:
            self.mqtt.publish(self.cycle_topic, value, qos=1, retain=True)
            self.log.info("cycle_active → %s", value)
        except Exception as e:
            self.log.warning("cycle_active publish: %s", e)

    def reassert_availability(self):
        if self.session is None or self.last_state_pub is None:
            return
        self.set_availability(True)
        field = self.descriptor.remote_available_field
        if field is not None:
            self.publish_remote_available(
                self.last_state_pub.get(field), force=True)
        cycle_field = self.descriptor.cycle_active_field
        if cycle_field is not None:
            self.publish_cycle_active(
                self.last_state_pub.get(cycle_field), force=True)

    def set_availability(self, online):
        try:
            self.mqtt.publish(self.avail_topic,
                              'online' if online else 'offline',
                              qos=1, retain=True)
        except Exception as e:
            self.log.warning("avail publish: %s", e)
        if not online and self.descriptor.remote_available_field is not None:
            self.last_remote_pub = None
            try:
                self.mqtt.publish(self.remote_topic, 'offline',
                                  qos=1, retain=True)
            except Exception:
                pass
        if not online and self.descriptor.cycle_active_field is not None:
            self.last_cycle_pub = None
            try:
                self.mqtt.publish(self.cycle_topic, 'offline',
                                  qos=1, retain=True)
            except Exception:
                pass

    # ---- MQTT command handling --------------------------------------

    def handle_command(self, topic, payload):
        if not topic.startswith(self.cmd_topic_prefix):
            return
        suffix = topic[len(self.cmd_topic_prefix) - len('cmd/'):]
        handler = self.cmd_handlers.get(suffix)
        if handler is None:
            self.log.warning("unknown command topic: %s", topic)
            return
        # Shallow-snapshot self.links so the handler sees a consistent
        # view across the read-modify-write it may need to perform
        # (e.g. oven lamp / sound / fastpreheat all RMW /mode/vs/0
        # options). Inner reps are mutated only by the OBSERVE reader
        # thread; handlers that mutate items must deep-copy themselves.
        result = handler(payload, dict(self.links))
        if result is None:
            self.log.warning("rejected command %s payload=%r",
                             topic, payload)
            return
        path_segs, body = result
        sess = self.session
        if sess is None:
            self.log.warning("command %s: no DTLS session", topic)
            return
        try:
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=8.0)
        except Exception as e:
            self.log.warning("command %s POST failed: %s", topic, e)
            return
        self.log.info("command %s payload=%r → %s",
                      suffix, payload, fmt_code(code))
        if code >> 5 == 2:
            href = '/' + '/'.join(path_segs)
            # Optimistic publish — apply the write to our local state.
            # No fetchback: empirically (2026-05-31) the post-write
            # Block2 GET was causing the appliance to roll our values
            # back ~3s later, on every writable resource. OBSERVE
            # pushes keep HA in sync without polling. (Was the root
            # cause of the "mid-cycle setpoint reverts" symptom.)
            self._apply_optimistic(href, body)

    def publish_health(self):
        now = time.time()
        h = {
            'mode':              'push',
            'device_class':      self.descriptor.name,
            'serial':            self._serial,
            'connect_count':     self.connect_count,
            'error_count':       self.error_count,
            'notif_count':       self.notif_count,
            'last_change_age_s': (round(now - self.last_change_ts, 1)
                                  if self.last_change_ts else None),
            'last_seed_age_s':   (round(now - self.last_seed_ts, 1)
                                  if self.last_seed_ts else None),
            'session_age_s':     (round(now - self.session_started_ts, 1)
                                  if self.session_started_ts else None),
            'uptime_seconds':    round(now - self.started_ts, 0),
        }
        try:
            self.mqtt.publish(self.health_topic, json.dumps(h).encode(),
                              qos=0, retain=True)
        except Exception as e:
            self.log.warning("health publish: %s", e)

    # ---- top-level loop ---------------------------------------------

    def _publish_tick_loop(self):
        while not self.stop.wait(15.0):
            try:
                self.maybe_publish_state()
            except Exception as e:
                self.log.warning("publish tick: %s", e)

    def ping_once(self):
        """Send one CoAP Ping. Caller (main.py's per-bridge ping
        thread) handles the cadence. No-op if no live session."""
        sess = self.session
        if sess is None:
            return
        try:
            sess.ping()
        except Exception as e:
            self.log.warning("ping: %s", e)

    def run_forever(self):
        threading.Thread(target=self._publish_tick_loop, daemon=True,
                         name=f'{self.app.klass}-tick').start()
        backoff = 1.0
        while not self.stop.is_set():
            try:
                self.session_once()
                backoff = 1.0
            except Exception as e:
                self.error_count += 1
                self.log.warning("session error: %s", e)
            sess = self.session
            self.session = None
            if sess is not None:
                try: sess.close()
                except Exception: pass
            self.set_availability(False)
            self.session_started_ts = None
            if self.stop.is_set():
                break
            wait = min(backoff, 30.0)
            self.log.info("reconnect in %.0fs", wait)
            if self.stop.wait(wait):
                break
            backoff = min(backoff * 2, 30.0)
