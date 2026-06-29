"""Adapter: a discovered BoundEntity list -> a RuntimeDescriptor the bridge
consumes. Replaces the per-appliance flatten/build_discovery/command_handlers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..poll_scheduler import PollTier
from .discovery import BoundEntity
from .entities import (
    BinarySensorDesc, ButtonDesc, NumberDesc, PLATFORM_OF, SelectDesc,
    SensorDesc, SwitchDesc,
)

_TIER_CADENCE = {'hot': (1.0, 0.5), 'warm': (15.0, None), 'cold': (300.0, None)}

# Keys produced by oven capabilities that require cycle-active gating.
# When any of these appear in the produced set, the runtime descriptor
# exposes cycle_active_field='cycle_active' so the bridge can gate writes.
_CYCLE_GATED_KEYS = {'oven_setpoint', 'oven_mode', 'cook_time'}


def _segs(href: str) -> list[str]:
    return [s for s in href.strip('/').split('/') if s]


def _key(b: BoundEntity) -> str:
    return f"{b.desc.key}{b.instance}"


def _encode(cfg: dict) -> bytes:
    return json.dumps(cfg).encode()


@dataclass
class RuntimeDescriptor:
    name: str
    seed_path: list[str]
    default_observe_port: int
    observe_paths: list[list[str]]
    poll_tiers: list[PollTier]
    is_active: Optional[Callable[[dict], bool]]
    flatten: Callable[[dict], dict]
    discovery_payloads: list[tuple[str, bytes]]
    command_handlers: Callable[[], dict]
    project: Optional[Callable[[dict, dict], dict]]
    on_observation: Optional[Callable[[dict, str, dict], None]]
    remote_available_field: Optional[str]
    cycle_active_field: Optional[str]
    log_state_change: Optional[Callable[[dict], str]]


def _make_flatten(bound: list[BoundEntity]) -> Callable[[dict], dict]:
    reads = [b for b in bound if b.desc.field]   # buttons have no field

    def flatten(links: dict) -> dict:
        out: dict = {}
        for b in reads:
            rep = links.get(b.href) or {}
            if b.desc.exists_fn is not None and not b.desc.exists_fn(rep):
                continue
            out[_key(b)] = b.desc.value_fn(rep.get(b.desc.field))
        return out

    return flatten


def _make_observe_paths(bound: list[BoundEntity]) -> list[list[str]]:
    seen, out = set(), []
    for b in bound:
        if not b.capability.observe or b.href in seen:
            continue
        seen.add(b.href)
        out.append(_segs(b.href))
    return out


def _make_poll_tiers(bound: list[BoundEntity]) -> list[PollTier]:
    groups: dict[str, set[str]] = {}
    for b in bound:
        groups.setdefault(b.capability.poll_tier, set()).add(b.href)
    tiers: list[PollTier] = []
    for name, hrefs in groups.items():
        interval, active = _TIER_CADENCE.get(name, (15.0, None))
        tiers.append(PollTier(
            name=name, interval_s=interval, active_interval_s=active,
            paths=tuple(tuple(_segs(h)) for h in sorted(hrefs))))
    tiers.append(PollTier(name='sweep', interval_s=300.0,
                          paths=(('device', '0'),), is_sweep=True))
    return tiers


def _make_is_active(bound):
    caps = {b.href: b.capability for b in bound if b.capability.active_when}
    if not caps:
        return None

    def is_active(links: dict) -> bool:
        return any(cap.active_when(links.get(href) or {})
                   for href, cap in caps.items())

    return is_active


def _make_on_observation(bound):
    caps = {b.href: b.capability for b in bound if b.capability.on_observation}
    if not caps:
        return None

    def on_observation(state: dict, href: str, rep: dict) -> None:
        cap = caps.get(href)
        if cap is not None:
            cap.on_observation(state, rep)

    return on_observation


def _make_project(bound):
    projs = []
    seen = set()
    for b in bound:
        if b.capability.project and b.capability.href not in seen:
            seen.add(b.capability.href)
            projs.append(b.capability.project)
    if not projs:
        return None

    def project(state: dict, sensors: dict) -> dict:
        for p in projs:
            sensors = p(state, sensors)
        return sensors

    return project


def _make_command_handlers(bound):
    writers = [b for b in bound if getattr(b.desc, 'write_fn', None)]

    def command_handlers() -> dict:
        out = {}
        for b in writers:
            href, fn = b.href, b.desc.write_fn

            def handler(payload, links, _fn=fn, _href=href):
                rep = (links or {}).get(_href) or {}
                return _fn(payload, rep)

            out[f"cmd/{_key(b)}"] = handler
        return out

    return command_handlers


# --- MQTT discovery config emitters (per platform) ------------------------
def _avail(topic_prefix):
    return [{'topic': f"{topic_prefix}/availability",
             'payload_available': 'online', 'payload_not_available': 'offline'}]


def _device(topic_prefix, device_name, model):
    return {'identifiers': [topic_prefix], 'name': device_name,
            'manufacturer': 'Samsung', 'model': model}


def _discovery_payloads(bound, topic_prefix, ha_prefix, device_name, model):
    state_topic = f"{topic_prefix}/state"
    dev = _device(topic_prefix, device_name, model)
    avail = _avail(topic_prefix)
    out = []
    for b in bound:
        d = b.desc
        key = _key(b)
        platform = PLATFORM_OF[type(d)]
        uid = f"{topic_prefix}_{key}"
        cfg = {'name': d.name or key, 'unique_id': uid, 'object_id': uid,
               'device': dev, 'availability': avail}
        if d.icon:
            cfg['icon'] = d.icon
        if d.entity_category:
            cfg['entity_category'] = d.entity_category
        if isinstance(d, SensorDesc):
            cfg['state_topic'] = state_topic
            cfg['value_template'] = f"{{{{ value_json.{key} }}}}"
            for k, attr in (('unit_of_measurement', 'unit'),
                            ('device_class', 'device_class'),
                            ('state_class', 'state_class')):
                if getattr(d, attr):
                    cfg[k] = getattr(d, attr)
        elif isinstance(d, BinarySensorDesc):
            cfg['state_topic'] = state_topic
            cfg['value_template'] = (
                f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}")
            cfg['payload_on'] = 'ON'
            cfg['payload_off'] = 'OFF'
            if d.device_class:
                cfg['device_class'] = d.device_class
        elif isinstance(d, SelectDesc):
            cfg['state_topic'] = state_topic
            cfg['value_template'] = f"{{{{ value_json.{key} }}}}"
            cfg['command_topic'] = f"{topic_prefix}/cmd/{key}"
            opts = d.options
            cfg['options'] = list(opts) if isinstance(opts, (list, tuple)) else []
        elif isinstance(d, SwitchDesc):
            cfg['state_topic'] = state_topic
            cfg['value_template'] = f"{{% if value_json.{key} %}}On{{% else %}}Off{{% endif %}}"
            cfg['state_on'] = 'On'
            cfg['state_off'] = 'Off'
            cfg['payload_on'] = 'On'
            cfg['payload_off'] = 'Off'
            cfg['command_topic'] = f"{topic_prefix}/cmd/{key}"
        elif isinstance(d, ButtonDesc):
            cfg['command_topic'] = f"{topic_prefix}/cmd/{key}"
            cfg['payload_press'] = d.payload
        elif isinstance(d, NumberDesc):
            cfg['state_topic'] = state_topic
            cfg['value_template'] = f"{{{{ value_json.{key} }}}}"
            cfg['command_topic'] = f"{topic_prefix}/cmd/{key}"
            for k, attr in (('unit_of_measurement', 'unit'),
                            ('device_class', 'device_class'),
                            ('min', 'native_min'), ('max', 'native_max'),
                            ('step', 'step')):
                if getattr(d, attr) is not None:
                    cfg[k] = getattr(d, attr)
        out.append((f"{ha_prefix}/{platform}/{topic_prefix}/{key}/config",
                    _encode(cfg)))
    return out


def build_runtime_descriptor(bound, *, topic_prefix, ha_prefix, device_name,
                             model, name, default_port) -> RuntimeDescriptor:
    produced = {_key(b) for b in bound}
    return RuntimeDescriptor(
        name=name,
        seed_path=['device', '0'],
        default_observe_port=default_port,
        observe_paths=_make_observe_paths(bound),
        poll_tiers=_make_poll_tiers(bound),
        is_active=_make_is_active(bound),
        flatten=_make_flatten(bound),
        discovery_payloads=_discovery_payloads(
            bound, topic_prefix, ha_prefix, device_name, model),
        command_handlers=_make_command_handlers(bound),
        project=_make_project(bound),
        on_observation=_make_on_observation(bound),
        remote_available_field='remote_control' if 'remote_control' in produced else None,
        cycle_active_field='cycle_active' if produced & _CYCLE_GATED_KEYS else None,
        log_state_change=None,
    )
