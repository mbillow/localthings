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

from .appliances.base import ApplianceDescriptor, bridge_diagnostic_discovery
from .coap_dtls import DtlsCoapSession, fmt_code
from .config import ApplianceConfig, SharedConfig
from .keepalive import KeepaliveTask
from .logger import bridge_logger
from .observe_refresh import ObserveRefreshTask
from .poll_scheduler import PollScheduler
from .sensors import index_links
from .state_cache import StateCache


DEBUG_BRIDGE = os.environ.get('DEBUG_BRIDGE') == '1'


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


class PushBridge:

    def __init__(self,
                 shared: SharedConfig,
                 app: ApplianceConfig,
                 descriptor: ApplianceDescriptor,
                 mqtt_client):
        self.shared = shared
        self.app = app
        self.descriptor = descriptor
        self.mqtt = mqtt_client

        self.log = bridge_logger(app.klass)
        self._serial: str | None = None

        self.port = app.ocf_port or descriptor.default_observe_port

        self.session: DtlsCoapSession | None = None
        self.scheduler: PollScheduler | None = None
        self.keepalive: KeepaliveTask | None = None
        self.observe_refresh: ObserveRefreshTask | None = None

        self.cache = StateCache(descriptor)
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
        self.cmd_handlers   = descriptor.command_handlers()
        self.cmd_topic_prefix = f"{p}/cmd/"

        self.discovery_payloads = (
            descriptor.build_discovery(
                app.topic_prefix, shared.HA_DISCOVERY_PREFIX, app.device_name)
            + bridge_diagnostic_discovery(
                app.topic_prefix, shared.HA_DISCOVERY_PREFIX, app.device_name,
                model=descriptor.name.title()))

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

        self.log.info("DTLS connected — subscribing %d paths",
                      len(self.descriptor.observe_paths))

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
        for path in self.descriptor.observe_paths:
            sess.subscribe(path)
            time.sleep(0.05)

        # Inline seed so the publish gate opens before the scheduler's
        # first tick. The scheduler's sweep tier will refresh /device/0
        # on its own cadence afterwards.
        self._seed_from_device0(sess)
        self._retag_logger_with_serial()

        if DEBUG_BRIDGE:
            self._debug_dump_links(sess)

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
        # During the seed we want the cache populated without triggering
        # a publish per resource — gate the on_change callback off until
        # the publish gate opens just below.
        for href, rep in index_links(body).items():
            if href not in self.cache.links:
                self.cache.apply_rep(href, rep, source='seed')
        self.last_seed_ts = time.time()

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
            'device_class':              self.descriptor.name,
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
