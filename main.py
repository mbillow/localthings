#!/usr/bin/env python3
"""SmartThings-Local Bridge — entry point.

One process supervises N Samsung appliances over their OCF CoAP-DTLS
local APIs, publishing state + HA discovery to MQTT. Each appliance
runs its own DTLS session in its own thread, with an in-session
PollScheduler driving state freshness and a KeepaliveTask driving
DTLS-layer liveness. MQTT is shared.

Config is env-var driven:
  * APPLIANCE_COUNT plus APPLIANCE_<n>_{CLASS,IP,OCF_PORT,TOPIC,NAME}
    define the appliances to bridge.
  * Shared keys (MQTT_*, HA_DISCOVERY_PREFIX, CERT_PATH, KEY_PATH,
    HEALTH_INTERVAL_S, PING_INTERVAL_S) apply to all.

Reconnects on session errors per-appliance; shuts down cleanly on
SIGINT / SIGTERM."""
import logging
import os
import signal
import sys
import threading

import paho.mqtt.client as mqtt

from samsung_appliance.appliances import get_descriptor
from samsung_appliance.bridge import PushBridge
from samsung_appliance.config import SharedConfig, load_appliances
from samsung_appliance.logger import logger


def main():
    shared = SharedConfig.from_env()

    try:
        appliances = load_appliances()
    except ValueError as e:
        logger.error("config: %s", e)
        return 2

    if not shared.MQTT_BROKER:
        logger.error("config: MQTT_BROKER not set")
        return 2
    for path in (shared.CERT_PATH, shared.KEY_PATH):
        if not path.exists():
            logger.error("client cert/key not found: %s", path)
            return 2

    # Resolve descriptors up front — bad APPLIANCE_<n>_CLASS should fail
    # at startup, not 10s into the first DTLS attempt.
    pairs = []
    for app in appliances:
        try:
            desc = get_descriptor(app.klass)
        except ValueError as e:
            logger.error("APPLIANCE_%d_CLASS: %s", app.index, e)
            return 2
        pairs.append((app, desc))

    logger.info("SmartThings-Local Bridge starting (%d appliance%s)",
                len(appliances), '' if len(appliances) == 1 else 's')
    logger.info("  broker = %s:%d (user=%s)",
                shared.MQTT_BROKER, shared.MQTT_PORT,
                shared.MQTT_USER or '<anon>')
    for app, desc in pairs:
        port = app.ocf_port or desc.default_observe_port
        logger.info("  [%d] %s @ %s:%d (DTLS) → topic %s/*",
                    app.index, app.klass, app.ip, port, app.topic_prefix)

    # --- MQTT client (shared) ---
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                      client_id='smartthings_local_bridge')
    if shared.MQTT_USER:
        cli.username_pw_set(shared.MQTT_USER, shared.MQTT_PASS)
    # Headroom for the on_connect burst across all appliances (each
    # publishes ~30 retained QoS-1 messages: discovery + state + avail).
    # 100 + 50*N keeps us comfortably ahead of paho's default 20.
    cli.max_inflight_messages_set(100 + 50 * len(appliances))

    # Use the FIRST appliance's availability topic for LWT — paho only
    # supports one will message. If a second appliance is added later,
    # its availability is managed via explicit publishes on disconnect
    # rather than LWT. For Phase 1 (dryer only) this is exact.
    first_app = appliances[0]
    cli.will_set(f"{first_app.topic_prefix}/availability",
                 payload='offline', qos=1, retain=True)

    # Build bridges. Each builds its own discovery payloads in __init__.
    bridges: list[PushBridge] = [PushBridge(shared, app, desc, cli)
                                 for app, desc in pairs]
    by_prefix = {b.cmd_topic_prefix.rstrip('/'): b for b in bridges}

    def on_connect(client, userdata, flags, rc, props=None):
        if rc != 0:
            logger.warning("MQTT connect rc=%s", rc)
            return
        logger.info("MQTT connected → %s:%d",
                    shared.MQTT_BROKER, shared.MQTT_PORT)
        for b in bridges:
            for topic, payload in b.discovery_payloads:
                client.publish(topic, payload, qos=1, retain=True)
            cmd_wildcard = f"{b.app.topic_prefix}/cmd/#"
            client.subscribe(cmd_wildcard, qos=1)
            b.reassert_availability()
        logger.info("subscribed to %d cmd wildcards", len(bridges))

    def on_disconnect(client, userdata, flags, rc, props=None):
        logger.warning("MQTT disconnected rc=%s", rc)

    def on_message(client, userdata, msg):
        # Route by topic prefix. Each appliance owns a distinct
        # `<prefix>/cmd/*` namespace, so the prefix-match is unambiguous.
        for prefix, bridge in by_prefix.items():
            if msg.topic.startswith(prefix + '/'):
                try:
                    payload = msg.payload.decode('utf-8',
                                                 errors='replace').strip()
                except Exception:
                    return
                bridge.handle_command(msg.topic, payload)
                return

    cli.on_connect = on_connect
    cli.on_disconnect = on_disconnect
    cli.on_message = on_message

    if os.getenv('PAHO_DEBUG'):
        paho_logger = logging.getLogger('paho.mqtt.client')
        paho_logger.setLevel(logging.DEBUG)
        paho_handler = logging.StreamHandler(sys.stdout)
        paho_handler.setFormatter(logging.Formatter(
            '%(asctime)s  PAHO   %(message)s', datefmt='%H:%M:%S'))
        paho_logger.addHandler(paho_handler)
        paho_logger.propagate = False
        cli.enable_logger(paho_logger)

    cli.connect_async(shared.MQTT_BROKER, shared.MQTT_PORT, keepalive=60)
    cli.loop_start()

    # Per-bridge runner / health / heartbeat threads.
    threads: list[threading.Thread] = []

    def make_health(b: PushBridge):
        def loop():
            while not b.stop.is_set():
                b.publish_health()
                if b.stop.wait(shared.HEALTH_INTERVAL_S):
                    break
        return loop

    for b in bridges:
        tag = b.app.klass
        threads.append(threading.Thread(
            target=b.run_forever, daemon=True, name=f'{tag}-session'))
        threads.append(threading.Thread(
            target=make_health(b), daemon=True, name=f'{tag}-health'))

    stopping = threading.Event()

    def shutdown(*_):
        if stopping.is_set():
            return
        stopping.set()
        logger.info("shutting down…")
        for b in bridges:
            b.stop.set()
            try: b.set_availability(False)
            except Exception: pass

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    for t in threads:
        t.start()

    # Wait for the session threads (the only non-daemon-equivalent
    # loops). They exit when their bridge's stop event is set.
    try:
        for t in threads:
            if t.name.endswith('-session'):
                t.join()
    finally:
        try:
            cli.loop_stop()
            cli.disconnect()
        except Exception:
            pass
        logger.info("stopped")
    return 0


if __name__ == '__main__':
    sys.exit(main())
