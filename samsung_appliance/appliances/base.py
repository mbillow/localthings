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
from typing import Callable, Optional


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

    # Optional log-line callback for state-change notifications. Gets
    # the freshly-projected sensors dict; returns a short string.
    log_state_change: Optional[Callable[[dict], str]] = None


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


def encode(cfg: dict) -> bytes:
    return json.dumps(cfg).encode()
