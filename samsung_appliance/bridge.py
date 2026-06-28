"""Bridge: OCF CoAP-DTLS appliance → MQTT, polling-first with opportunistic OBSERVE.

  Appliance ──CoAP DTLS─►  PushBridge ──MQTT──►  Home Assistant

State freshness comes from a tiered PollScheduler over a persistent DTLS
session. OBSERVE registrations are kept as an opportunistic freshness
accelerator — when the appliance has internet and pushes notifications,
the cache absorbs them; when it's air-gapped, polling carries the UX
unchanged.

DTLS-layer liveness is a separate KeepaliveTask (CoAP empty-CON ping
every PING_INTERVAL_S). Three consecutive failures publish MQTT
availability=offline.

Multiple PushBridges run concurrently — see main.py. They share one
MQTT client; each owns one DTLS session, one cache, one scheduler.
"""
import json
import os
import random
import threading
import time

import cbor2

from .coap_dtls import DtlsCoapSession, fmt_code
from .config import ApplianceConfig, SharedConfig
from .keepalive import KeepaliveTask
from .logger import bridge_logger
from .observe_refresh import ObserveRefreshTask
from .poll_scheduler import PollScheduler
from .sensors import index_links
from .state_cache import StateCache


DEBUG_BRIDGE = os.environ.get('DEBUG_BRIDGE') == '1'

# /device/0 is the OCF resource that contains the full link dict used
# to seed the state cache on every connect.
SEED_PATH = ['device', '0']


def _href_to_segs(href: str) -> list[str]:
    return [s for s in href.split('/') if s]


SERIAL_PATH = '/information/vs/0'
SERIAL_FIELD = 'x.com.samsung.da.serialNum'

# If the keepalive watchdog flags the device unreachable for this long,
# force a session reconnect. Catches the half-open case where the DTLS
# socket is still writable but the peer has gone silent — without this,
# the bridge sits in offline state until the reader thread dies on its
# own (which may not happen at all if the OS sees no socket errors).
UNREACHABLE_RECONNECT_S = 120.0

# Periodic OBSERVE re-subscribe interval. Safety net for the case where
# the device stays reachable on the DTLS layer but Samsung's RT-OCF
# clears its observer table (e.g. during cloud auth blips). Without
# this, push delivery stays dead even after upstream connectivity
# recovers, since nothing triggers a fresh subscribe on the existing
# session.
OBSERVE_REFRESH_INTERVAL_S = 6 * 3600.0


def _encode(cfg: dict) -> bytes:
    return json.dumps(cfg).encode()


def _bridge_diagnostic_discovery(topic_prefix: str,
                                  ha_discovery_prefix: str,
                                  device_name: str,
                                  model: str = 'Bridge') -> list[tuple[str, bytes]]:
    """HA-discovery payloads for the per-bridge diagnostic entities
    (push state, polling RTT, freshness). Returned as a list of
    (topic, payload-bytes) tuples to be merged with entity discovery."""
    avail_topic  = f"{topic_prefix}/availability"
    health_topic = f"{topic_prefix}/bridge/health"
    push_topic   = f"{topic_prefix}/bridge/push_active"
    device = {
        'identifiers':  [topic_prefix],
        'name':         device_name,
        'manufacturer': 'Samsung',
        'model':        model,
    }
    avail = [{'topic': avail_topic,
              'payload_available':     'online',
              'payload_not_available': 'offline'}]

    def sensor(slug: str, name: str, value_template: str,
               unit: str | None = None, device_class: str | None = None,
               icon: str | None = None) -> tuple[str, bytes]:
        cfg = {
            'name':              name,
            'unique_id':         f"{topic_prefix}_bridge_{slug}",
            'object_id':         f"{topic_prefix}_bridge_{slug}",
            'state_topic':       health_topic,
            'value_template':    value_template,
            'availability':      avail,
            'device':            device,
            'entity_category':   'diagnostic',
        }
        if unit is not None:         cfg['unit_of_measurement'] = unit
        if device_class is not None: cfg['device_class']        = device_class
        if icon is not None:         cfg['icon']                = icon
        topic = (f"{ha_discovery_prefix}/sensor/"
                 f"{topic_prefix}/bridge_{slug}/config")
        return topic, _encode(cfg)

    push_active_cfg = {
        'name':              'Push Active',
        'unique_id':         f"{topic_prefix}_bridge_push_active",
        'object_id':         f"{topic_prefix}_bridge_push_active",
        'state_topic':       push_topic,
        'payload_on':        'online',
        'payload_off':       'offline',
        'availability':      avail,
        'device':            device,
        'entity_category':   'diagnostic',
        'icon':              'mdi:rss',
    }
    push_active_topic = (f"{ha_discovery_prefix}/binary_sensor/"
                         f"{topic_prefix}/bridge_push_active/config")

    return [
        (push_active_topic, _encode(push_active_cfg)),
        sensor('update_source', 'Last Update Source',
               "{{ value_json.last_change_source | default('?') }}",
               icon='mdi:transit-connection-variant'),
        sensor('stalest_age_s', 'Stalest Resource Age',
               "{{ value_json.stalest_age_s | default(0) }}",
               unit='s', icon='mdi:clock-alert-outline'),
        sensor('poll_max_rtt_ms', 'Poll Max RTT',
               "{{ value_json.poll_window_max_rtt_ms | default(0) | int }}",
               unit='ms', icon='mdi:speedometer'),
        sensor('slow_polls', 'Slow Polls (window)',
               "{{ value_json.poll_window_slow_count | default(0) }}",
               icon='mdi:timer-sand'),
        sensor('poll_errors', 'Poll Errors (window)',
               "{{ value_json.poll_window_errors | default(0) }}",
               icon='mdi:alert-circle-outline'),
        sensor('observe_age_s', 'Last OBSERVE Age',
               "{{ value_json.last_observe_age_s if value_json.last_observe_age_s is not none else 'never' }}",
               icon='mdi:radar'),
    ]


class PushBridge:

    def __init__(self,
                 shared: SharedConfig,
                 app: ApplianceConfig,
                 mqtt_client):
        self.shared = shared
        self.app = app
        self.mqtt = mqtt_client

        self.log = bridge_logger(app.klass)
        self._serial: str | None = None

        self.port = app.ocf_port or 49154

        self.session: DtlsCoapSession | None = None
        self.scheduler: PollScheduler | None = None
        self.keepalive: KeepaliveTask | None = None
        self.observe_refresh: ObserveRefreshTask | None = None

        # descriptor is None until _discover_and_publish completes after seed.
        self.descriptor = None
        self._discovered = False
        self._entity_payloads: list = []

        self.cache = StateCache()
        self.cache.set_on_change(self._on_cache_change)

        self.last_state_pub = None
        self.last_remote_pub = None
        self.last_cycle_pub = None
        self.last_avail_pub: str | None = None
        self.stop = threading.Event()
        self.started_ts = time.time()
        self.session_started_ts = None
        self.last_change_ts = None
        self.last_seed_ts = None
        self.notif_count = 0
        self.connect_count = 0
        self.error_count = 0
        self._publish_gate = False
        self._last_change_source: str | None = None
        self._last_observe_change_ts: float | None = None
        self._last_push_active_pub: str | None = None
        # Wall-clock timestamp the keepalive watchdog first reported the
        # device unreachable on the current session. Cleared on recovery
        # or session start. Drives the force-reconnect watchdog below.
        self._unreachable_since: float | None = None
        self._force_close_in_flight: bool = False

        # Push is considered "active" if an OBSERVE-sourced change
        # arrived within this window. Long enough that a quiet but
        # working appliance doesn't flap to inactive; short enough that
        # a genuinely silent push channel is visible within minutes.
        self.push_active_window_s = 600.0

        # Snapshot counters for the per-health-window deltas surfaced in
        # publish_health's log summary.
        self._win_prev_poll = 0
        self._win_prev_poll_err = 0
        self._win_prev_ping_fail = 0

        # Per-href fetchback generation counter coalesces bursts of
        # OBSERVE-block2 partial notifications: rapid changes scheduled
        # many fetchbacks but only the latest actually publishes.
        self._fetch_gen: dict[str, int] = {}
        self._fetch_lock = threading.Lock()

        p = app.topic_prefix
        self.state_topic    = f"{p}/state"
        self.avail_topic    = f"{p}/availability"
        self.remote_topic   = f"{p}/remote_available"
        self.cycle_topic    = f"{p}/cycle_active"
        self.health_topic   = f"{p}/bridge/health"
        self.push_active_topic = f"{p}/bridge/push_active"
        self.cmd_handlers   = {}
        self.cmd_topic_prefix = f"{p}/cmd/"

    # ---- cache plumbing ---------------------------------------------

    def _on_cache_change(self, changed: bool, source: str) -> None:
        if changed:
            self.notif_count += 1
            self.last_change_ts = time.time()
            self._last_change_source = source
            if source == 'observe':
                self._last_observe_change_ts = self.last_change_ts
        self.maybe_publish_state()

    def _on_notification(self, href, payload_bytes):
        """Reader-thread callback for OBSERVE notifications. Large
        resources (oven /mode/vs/0 ~9KB) arrive truncated with Block2.M=1
        and we use cbor-decode failure as the partial signal."""
        if not payload_bytes:
            self._schedule_fetchback(href)
            return
        try:
            rep = cbor2.loads(payload_bytes)
        except Exception:
            self._schedule_fetchback(href)
            return
        if not isinstance(rep, dict):
            return
        if DEBUG_BRIDGE:
            self._debug_log_rep(href, rep)
        self.cache.apply_rep(href, rep, source='observe')

    def _debug_log_rep(self, href, rep):
        if href == '/mode/vs/0' and isinstance(rep, dict):
            self.log.info("mode modes=%r options=%r",
                          rep.get('x.com.samsung.da.modes'),
                          rep.get('x.com.samsung.da.options'))
        elif href in ('/operational/state/vs/0', '/oven/vs/0', '/power/vs/0'):
            self.log.info("REP %s = %r", href, rep)

    def _schedule_fetchback(self, href, delay_s: float = 0.0):
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
        if delay_s > 0 and self.stop.wait(delay_s):
            return
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
        with self._fetch_lock:
            if self._fetch_gen.get(href) != gen:
                return
        if code != 0x45:
            self.log.warning("fetchback %s: %s", href, fmt_code(code))
            return
        try:
            rep = cbor2.loads(payload) if payload else {}
        except Exception as e:
            self.log.warning("fetchback %s cbor: %s", href, e)
            return
        if isinstance(rep, dict):
            self.cache.apply_rep(href, rep, source='observe')

    def _retag_logger_with_serial(self):
        if self._serial is not None:
            return
        info = self.cache.get(SERIAL_PATH) or {}
        serial = info.get(SERIAL_FIELD)
        if not serial:
            return
        self._serial = serial
        self.log = bridge_logger(self.app.klass, serial)
        self.log.info("identified — serial=%s", serial)

    # ---- session lifecycle ------------------------------------------

    def session_once(self):
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
        self.cache.descriptor_state.clear()
        self._publish_gate = False
        self._unreachable_since = None
        self._force_close_in_flight = False

        self.log.info("DTLS connected — port %d", self.port)

        sess.start_reader()

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
            session_ended.set()

    def _run_session_inner(self, sess):
        # Seed first — populate cache before discovery can run.
        # The publish gate stays closed so seed writes don't trigger
        # per-resource publishes before the full snapshot is ready.
        self._seed_from_device0(sess)
        self._retag_logger_with_serial()

        if DEBUG_BRIDGE:
            self._debug_dump_links(sess)

        # Discover entities from the seeded cache, build the runtime
        # descriptor, and publish HA discovery payloads.  Idempotent:
        # on reconnect the descriptor is reused from the first session.
        self._discover_and_publish(sess)

        # Now subscribe to observe paths (descriptor populated above).
        for path in self.descriptor.observe_paths:
            sess.subscribe(path)
            time.sleep(0.05)

        self._publish_gate = True
        self.maybe_publish_state(force=True)
        self.set_availability(True)
        self.log.info("seeded → %d links; sensors live",
                      len(self.cache.links))

        scheduler = PollScheduler(
            sess, self.cache,
            tiers=self.descriptor.poll_tiers,
            sweep_index_fn=index_links,
            is_active_fn=self.descriptor.is_active,
            logger=self.log,
        )
        # Half-open detection: if no successful poll lands inside this
        # window the session is wedged, regardless of whether ping sends
        # leave the socket. 60s gives ~60 hot-tier cycles of margin.
        liveness_window_s = 60.0
        keepalive = KeepaliveTask(
            sess,
            interval_s=float(self.shared.PING_INTERVAL_S),
            fail_threshold=3,
            on_reachable=self._on_reachable,
            on_unreachable=self._on_unreachable,
            logger=self.log,
            liveness_fn=lambda: (time.monotonic() - scheduler.last_success_ts
                                 ) < liveness_window_s,
        )
        observe_refresh = ObserveRefreshTask(
            sess,
            paths=self.descriptor.observe_paths,
            interval_s=OBSERVE_REFRESH_INTERVAL_S,
            logger=self.log,
        )
        self.scheduler = scheduler
        self.keepalive = keepalive
        self.observe_refresh = observe_refresh

        sched_t = threading.Thread(
            target=scheduler.run_forever, args=(self.stop,),
            daemon=True, name=f'{self.app.klass}-poll')
        ka_t = threading.Thread(
            target=keepalive.run_forever, args=(self.stop,),
            daemon=True, name=f'{self.app.klass}-ping')
        ref_t = threading.Thread(
            target=observe_refresh.run_forever, args=(self.stop,),
            daemon=True, name=f'{self.app.klass}-obsref')
        sched_t.start()
        ka_t.start()
        ref_t.start()

        try:
            sess.join()
        finally:
            self.scheduler = None
            self.keepalive = None
            self.observe_refresh = None

    def _seed_from_device0(self, sess):
        code, pl = sess.get(SEED_PATH, timeout=15.0)
        if code != 0x45:
            raise RuntimeError(
                f"/{'/'.join(SEED_PATH)} -> {fmt_code(code)}")
        try:
            body = cbor2.loads(pl)
        except Exception as e:
            raise RuntimeError(
                f"/{'/'.join(SEED_PATH)} cbor decode: {e}"
            ) from e
        # During the seed we want the cache populated without triggering
        # a publish per resource — gate the on_change callback off until
        # the publish gate opens just below.
        for href, rep in index_links(body).items():
            if href not in self.cache.links:
                self.cache.apply_rep(href, rep, source='seed')
        self.last_seed_ts = time.time()

    def _discover_and_publish(self, sess):
        """Run entity discovery against the seeded cache and publish HA
        discovery payloads.  Idempotent — on reconnect the descriptor
        built during the first session is reused; entity payloads are
        re-published via reassert_availability instead."""
        if self._discovered:
            return
        from .registry import CAPABILITIES, discover, build_runtime_descriptor
        from .registry.identity import read_identity
        snapshot = self.cache.snapshot()
        bound = discover(snapshot, CAPABILITIES,
                         log=lambda m: self.log.debug("%s", m))
        ident = read_identity(sess, self._serial)
        model = ident.model or self.app.klass.title() or 'Samsung Appliance'
        device_name = self.app.device_name or ident.name or model
        self.descriptor = build_runtime_descriptor(
            bound,
            topic_prefix=self.app.topic_prefix,
            ha_prefix=self.shared.HA_DISCOVERY_PREFIX,
            device_name=device_name,
            model=model,
            name=(self.app.klass or 'appliance'),
            default_port=self.port)
        self.cache.set_on_observation(self.descriptor.on_observation)
        self.cmd_handlers = self.descriptor.command_handlers()
        diag_payloads = _bridge_diagnostic_discovery(
            self.app.topic_prefix, self.shared.HA_DISCOVERY_PREFIX,
            device_name, model=model)
        self._entity_payloads = self.descriptor.discovery_payloads + diag_payloads
        for topic, payload in self._entity_payloads:
            self.mqtt.publish(topic, payload, qos=1, retain=True)
        self._discovered = True
        self.log.info("discovered %d entities", len(self.descriptor.discovery_payloads))

    def _debug_dump_links(self, sess):
        for href, rep in sorted(self.cache.links.items()):
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

    def _on_reachable(self) -> None:
        self._unreachable_since = None
        self.set_availability(True)
        self.reassert_availability()

    def _on_unreachable(self) -> None:
        if self._unreachable_since is None:
            self._unreachable_since = time.time()
        self.set_availability(False)

    def _maybe_force_reconnect(self) -> None:
        """If the device has been unreachable for UNREACHABLE_RECONNECT_S,
        close the DTLS session to break run_forever's session_once() out
        of its sess.join() and trigger a fresh connect. Without this, a
        half-open session (writable socket, silent peer) holds the bridge
        in offline limbo until the OS surfaces a socket error."""
        if self._unreachable_since is None or self._force_close_in_flight:
            return
        elapsed = time.time() - self._unreachable_since
        if elapsed < UNREACHABLE_RECONNECT_S:
            return
        sess = self.session
        if sess is None:
            return
        self.log.warning(
            "unreachable for %.0fs — forcing session reconnect", elapsed)
        self._force_close_in_flight = True
        try:
            sess.close()
        except Exception as e:
            self.log.warning("force-close: %s", e)

    # ---- MQTT publishing --------------------------------------------

    def maybe_publish_state(self, force=False):
        if self.descriptor is None:
            return
        if not force and not self._publish_gate:
            return
        snap = self.cache.snapshot()
        sensors = self.descriptor.flatten(snap)
        project = self.descriptor.project
        if project is not None:
            sensors = project(self.cache.descriptor_state, sensors)
        if not force and sensors == self.last_state_pub:
            return
        if DEBUG_BRIDGE and self.last_state_pub is not None:
            diffs = {k: (self.last_state_pub.get(k), v)
                     for k, v in sensors.items()
                     if self.last_state_pub.get(k) != v}
            diffs.update({k: (self.last_state_pub.get(k), None)
                          for k in self.last_state_pub
                          if k not in sensors})
            if diffs:
                self.log.info("sensor diff: %s",
                              {k: f"{a!r} → {b!r}" for k, (a, b) in diffs.items()})
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
            self.log.info("state changed [%s] (%s notif#%d)",
                          self._last_change_source or '?',
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
        if self._discovered:
            for topic, payload in self._entity_payloads:
                try:
                    self.mqtt.publish(topic, payload, qos=1, retain=True)
                except Exception as e:
                    self.log.warning("re-assert discovery: %s", e)
        if self.descriptor is not None:
            field = self.descriptor.remote_available_field
            if field is not None:
                self.publish_remote_available(
                    self.last_state_pub.get(field), force=True)
            cycle_field = self.descriptor.cycle_active_field
            if cycle_field is not None:
                self.publish_cycle_active(
                    self.last_state_pub.get(cycle_field), force=True)
        now = time.time()
        active = (self._last_observe_change_ts is not None
                  and (now - self._last_observe_change_ts) <= self.push_active_window_s)
        self.publish_push_active(active, force=True)

    def set_availability(self, online):
        value = 'online' if online else 'offline'
        if value == self.last_avail_pub:
            return
        self.last_avail_pub = value
        try:
            self.mqtt.publish(self.avail_topic, value, qos=1, retain=True)
        except Exception as e:
            self.log.warning("avail publish: %s", e)
        if not online and self.descriptor is not None:
            if self.descriptor.remote_available_field is not None:
                self.last_remote_pub = None
                try:
                    self.mqtt.publish(self.remote_topic, 'offline',
                                      qos=1, retain=True)
                except Exception:
                    pass
            if self.descriptor.cycle_active_field is not None:
                self.last_cycle_pub = None
                try:
                    self.mqtt.publish(self.cycle_topic, 'offline',
                                      qos=1, retain=True)
                except Exception:
                    pass
        if not online:
            self._last_push_active_pub = None
            try:
                self.mqtt.publish(self.push_active_topic, 'offline',
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
        # Handler gets a links snapshot so its read-modify-write sees a
        # consistent view across the multi-field operation.
        result = handler(payload, self.cache.snapshot())
        if result is None:
            self.log.warning("rejected command %s payload=%r",
                             topic, payload)
            return
        path_segs, body = result
        sess = self.session
        if sess is None:
            self.log.warning("command %s: no DTLS session", topic)
            return
        href = '/' + '/'.join(path_segs)
        sched = self.scheduler
        defer_s = 4.0
        if sched is not None:
            sched.write_in_progress(href, settle_s=defer_s)
        try:
            code, _ = sess.post(path_segs, cbor2.dumps(body), timeout=8.0)
        except Exception as e:
            self.log.warning("command %s POST failed: %s", topic, e)
            return
        defer_note = f" (poll-defer {href} {defer_s:.0f}s)" if sched is not None else ''
        self.log.info("command %s payload=%r → %s%s",
                      suffix, payload, fmt_code(code), defer_note)
        if code >> 5 == 2:
            # Optimistic local merge so HA sees the write reflected
            # immediately. No Block2 fetchback — that triggers Samsung's
            # 3-second revert (project_fetchback_revert_root_cause.md).
            # The PollScheduler will reconcile on its next tier tick
            # after the write_in_progress settle window expires.
            self.cache.apply_optimistic(href, body)

    def publish_health(self):
        now = time.time()
        sched = self.scheduler
        ka = self.keepalive

        last_obs_age = (round(now - self._last_observe_change_ts, 1)
                        if self._last_observe_change_ts else None)
        push_active = (last_obs_age is not None
                       and last_obs_age <= self.push_active_window_s)

        # Per-window deltas for the log summary AND the health topic
        # (HA gets the same numbers without doing template arithmetic).
        poll = sched.poll_count if sched else 0
        poll_err = sched.poll_error_count if sched else 0
        ping_fail = ka.ping_fail_count if ka else 0
        d_poll = poll - self._win_prev_poll
        d_err = poll_err - self._win_prev_poll_err
        d_ping_fail = ping_fail - self._win_prev_ping_fail
        self._win_prev_poll = poll
        self._win_prev_poll_err = poll_err
        self._win_prev_ping_fail = ping_fail
        win_max_rtt, win_slow, win_timeouts = (
            sched.take_window_stats() if sched else (0.0, 0, 0))
        window_polls_ok = max(0, d_poll - d_err)

        h = {
            'mode':                      'poll+observe',
            'device_class':              self.app.klass or 'appliance',
            'serial':                    self._serial,
            'connect_count':             self.connect_count,
            'error_count':               self.error_count,
            'notif_count':               self.notif_count,
            'poll_count':                poll,
            'poll_error_count':          poll_err,
            'poll_window_ok':            window_polls_ok,
            'poll_window_errors':        d_err,
            'poll_window_max_rtt_ms':    round(win_max_rtt, 0),
            'poll_window_slow_count':    win_slow,
            'poll_window_timeout_count': win_timeouts,
            'ping_count':                ka.ping_count if ka else 0,
            'ping_fail_count':           ping_fail,
            'reachable':                 ka.reachable if ka else None,
            'last_change_source':        self._last_change_source,
            'last_observe_age_s':        last_obs_age,
            'push_active':               push_active,
            'last_change_age_s':         (round(now - self.last_change_ts, 1)
                                          if self.last_change_ts else None),
            'last_seed_age_s':           (round(now - self.last_seed_ts, 1)
                                          if self.last_seed_ts else None),
            'session_age_s':             (round(now - self.session_started_ts, 1)
                                          if self.session_started_ts else None),
            'uptime_seconds':            round(now - self.started_ts, 0),
        }
        stalest = self.cache.stalest()
        if stalest is not None:
            h['stalest_href'] = stalest[0]
            h['stalest_age_s'] = round(stalest[1], 1)
        try:
            self.mqtt.publish(self.health_topic, json.dumps(h).encode(),
                              qos=0, retain=True)
        except Exception as e:
            self.log.warning("health publish: %s", e)

        self.publish_push_active(push_active)
        self._maybe_force_reconnect()

        if d_poll > 0 or d_err > 0 or d_ping_fail > 0:
            self.log.info(
                "poll-window: %d ok, %d err, %d ping-fail, "
                "p_max=%.0fms, slow=%d, timeouts=%d (%ds)",
                window_polls_ok, d_err, d_ping_fail,
                win_max_rtt, win_slow, win_timeouts,
                self.shared.HEALTH_INTERVAL_S)

    def publish_push_active(self, active: bool, force: bool = False) -> None:
        value = 'online' if active else 'offline'
        if not force and value == self._last_push_active_pub:
            return
        self._last_push_active_pub = value
        try:
            self.mqtt.publish(self.push_active_topic, value,
                              qos=1, retain=True)
            self.log.info("push_active → %s", value)
        except Exception as e:
            self.log.warning("push_active publish: %s", e)

    # ---- top-level loop ---------------------------------------------

    def run_forever(self):
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
            # Jitter the backoff so multiple bridges (dryer + oven) don't
            # reconnect in lockstep after a router blip — synchronized
            # storms make the broker / DTLS layer flap harder than need
            # be. ±30% noise spreads the retry attempts.
            wait = min(backoff, 30.0) * random.uniform(0.7, 1.3)
            self.log.info("reconnect in %.1fs", wait)
            if self.stop.wait(wait):
                break
            backoff = min(backoff * 2, 30.0)
