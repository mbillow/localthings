"""ApplianceDescriptor — the per-device-class abstraction.

The bridge is appliance-class-agnostic: it owns the DTLS session, the
OBSERVE-token bookkeeping, and MQTT publish gating. Each appliance
class (dryer, oven, …) provides a descriptor that supplies:

  * observe_paths       — which CoAP resources to OBSERVE
  * seed_path           — the resource to fetch on connect (usually
                          /device/0) to populate the link dict
  * flatten(links)      — links → flat-dict that lands on MQTT
  * build_discovery(…)  — list of (HA-discovery topic, payload)
  * command_handlers()  — MQTT command-suffix → (path_segs, body_dict)

Optional hooks let an appliance hold transient state across pushes:

  * on_observation(state, href, rep)   — capture anchors, e.g. for
                                          time extrapolation
  * project(state, sensors)            — fill in extrapolated fields
                                          on publish

state is a free-form dict the bridge owns and threads into both hooks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..poll_scheduler import PollTier


@dataclass
class ApplianceDescriptor:
    """Static description of a Samsung appliance class.

    `name` is the value DEVICE_CLASS resolves to (e.g. 'dryer').
    `default_observe_port` documents the UDP port this firmware
    exposes its DTLS-CoAP endpoint on, for the .env templates and
    docs — config.APPLIANCE_OCF_PORT still wins at runtime."""

    name: str
    default_observe_port: int

    observe_paths: list[list[str]]
    seed_path: list[str]

    flatten: Callable[[dict], dict]
    build_discovery: Callable[[str, str, str], list[tuple[str, bytes]]]
    # command_handlers() returns {topic_suffix: fn(payload_str, links_snapshot)}
    # where the fn returns (path_segs, body_dict) | None. Handlers receive
    # a snapshot of the bridge's link dict so they can do read-modify-write
    # on resources like `/mode/vs/0` options or `/temperatures/vs/0` items.
    command_handlers: Callable[[], dict[str, Callable[[str, dict], Optional[tuple]]]]

    # Optional behavioural hooks. state is a mutable dict the bridge
    # threads in; the descriptor decides what keys to put in it.
    on_observation: Optional[Callable[[dict, str, dict], None]] = None
    project: Optional[Callable[[dict, dict], dict]] = None

    # If set, the bridge maintains a second availability topic
    # `<prefix>/remote_available` derived from sensors[remote_field].
    # HA entities that the appliance only honours with Remote-Control
    # enabled gate themselves on it.
    remote_available_field: Optional[str] = None

    # If set, the bridge maintains a third availability topic
    # `<prefix>/cycle_active` derived from sensors[cycle_active_field]
    # (boolean). HA entities that the appliance only accepts writes for
    # while a cycle is running (e.g. oven setpoint, cook time, options
    # toggles) gate themselves on it.
    cycle_active_field: Optional[str] = None

    # Optional log-line callback for state-change notifications. Gets
    # the freshly-projected sensors dict; returns a short string.
    log_state_change: Optional[Callable[[dict], str]] = None

    # Tiered polling cadence. Hot tier resources are the user-visible
    # state that needs sub-second freshness; warm/cold/sweep cover the
    # rest. Empirically measured per-device ceilings in
    # local-tools/probe_poll_rate_combined.py (dryer ~14 req/s, oven
    # ~8 req/s) inform the defaults each descriptor sets.
    poll_tiers: list['PollTier'] = field(default_factory=list)

    # Predicate the PollScheduler calls each tick to decide whether to
    # use a tier's active_interval_s. Gets a shallow snapshot of the
    # link dict so it can read whichever resource indicates activity.
    is_active: Optional[Callable[[dict], bool]] = None


# --- HA-discovery helpers ----------------------------------------------
# Pure builder fns used by descriptor build_discovery() implementations.
# Kept here so the per-appliance modules stay focused on their entity
# inventory.

def device_block(topic_prefix: str, device_name: str,
                 model: str) -> dict:
    return {
        'identifiers':  [topic_prefix],
        'name':         device_name,
        'manufacturer': 'Samsung',
        'model':        model,
    }


def avail_base(avail_topic: str) -> list[dict]:
    return [{'topic': avail_topic,
             'payload_available':     'online',
             'payload_not_available': 'offline'}]


def avail_with_remote(avail_topic: str,
                      remote_topic: str) -> list[dict]:
    return [
        {'topic': avail_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
        {'topic': remote_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
    ]


def avail_with_cycle(avail_topic: str,
                     cycle_topic: str) -> list[dict]:
    return [
        {'topic': avail_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
        {'topic': cycle_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
    ]


def avail_with_remote_and_cycle(avail_topic: str,
                                remote_topic: str,
                                cycle_topic: str) -> list[dict]:
    return [
        {'topic': avail_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
        {'topic': remote_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
        {'topic': cycle_topic,
         'payload_available':     'online',
         'payload_not_available': 'offline'},
    ]


def encode(cfg: dict) -> bytes:
    return json.dumps(cfg).encode()


def bridge_diagnostic_discovery(topic_prefix: str,
                                ha_discovery_prefix: str,
                                device_name: str,
                                model: str = 'Bridge') -> list[tuple[str, bytes]]:
    """HA-discovery payloads for the per-bridge diagnostic entities
    (push state, polling RTT, freshness). Returned as a list of
    (topic, payload-bytes) tuples to be merged with the descriptor's
    own build_discovery output."""
    avail_topic  = f"{topic_prefix}/availability"
    health_topic = f"{topic_prefix}/bridge/health"
    push_topic   = f"{topic_prefix}/bridge/push_active"
    device       = device_block(topic_prefix, device_name, model)
    avail        = avail_base(avail_topic)

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
        return topic, encode(cfg)

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
        (push_active_topic, encode(push_active_cfg)),
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
        sensor('poll_timeouts', 'Poll Timeouts (window)',
               "{{ value_json.poll_window_timeout_count | default(0) }}",
               icon='mdi:timer-off-outline'),
        sensor('poll_errors', 'Poll Errors (window)',
               "{{ value_json.poll_window_errors | default(0) }}",
               icon='mdi:alert-circle-outline'),
        sensor('observe_age_s', 'Last OBSERVE Age',
               "{{ value_json.last_observe_age_s if value_json.last_observe_age_s is not none else 'never' }}",
               icon='mdi:radar'),
    ]
