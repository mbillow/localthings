"""Configuration — env-var driven.

Two flavours:
  * SharedConfig — MQTT broker, cert paths, HA prefix, timers. One per
    process.
  * ApplianceConfig — one per appliance the bridge is supervising. Keys
    come from `APPLIANCE_<n>_*` env vars (1-indexed). The list of
    appliances is `APPLIANCE_COUNT` entries long.

.env in cwd hydrates os.environ at import time; docker-compose env wins
over file contents."""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _load_env_file(filename='.env'):
    """If a .env-style file is present in cwd, hydrate os.environ from
    it. Existing env wins so docker-compose `environment:` overrides
    file contents."""
    env_path = Path.cwd() / filename
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip(); v = v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


_load_env_file('.env')


def _resolve_cert(env_key, basename):
    """Cert lookup: explicit env > /config/<basename> (Docker mount) >
    ./certs/<basename> (bare-metal dev)."""
    if os.getenv(env_key):
        return Path(os.environ[env_key])
    docker_path = Path('/config') / basename
    if docker_path.exists():
        return docker_path
    return Path.cwd() / 'certs' / basename


@dataclass(frozen=True)
class SharedConfig:
    """Process-wide config (MQTT, cert paths, intervals)."""
    CERT_PATH: Path
    KEY_PATH: Path
    MQTT_BROKER: Optional[str]
    MQTT_PORT: int
    MQTT_USER: Optional[str]
    MQTT_PASS: Optional[str]
    HA_DISCOVERY_PREFIX: str
    HEALTH_INTERVAL_S: int
    PING_INTERVAL_S: int

    @classmethod
    def from_env(cls) -> 'SharedConfig':
        return cls(
            CERT_PATH=_resolve_cert('CERT_PATH', 'ab0b0ac4_fullchain.pem'),
            KEY_PATH=_resolve_cert('KEY_PATH',  'ab0b0ac4.key'),
            MQTT_BROKER=os.getenv('MQTT_BROKER'),
            MQTT_PORT=int(os.getenv('MQTT_PORT', '1883')),
            MQTT_USER=os.getenv('MQTT_USER') or None,
            MQTT_PASS=os.getenv('MQTT_PASS') or None,
            HA_DISCOVERY_PREFIX=os.getenv('HA_DISCOVERY_PREFIX',
                                          'homeassistant'),
            HEALTH_INTERVAL_S=int(os.getenv('HEALTH_INTERVAL_S', '60')),
            PING_INTERVAL_S=int(os.getenv('PING_INTERVAL_S', '25')),
        )


@dataclass(frozen=True)
class ApplianceConfig:
    """Per-appliance runtime config.

    Sourced from `APPLIANCE_<index>_*` env vars (1-indexed). `index` is
    just a stable identifier for logs; it does not appear in MQTT
    topics or HA discovery (those are keyed off `topic_prefix`)."""
    index: int
    klass: str              # 'dryer', 'oven', …
    ip: str
    ocf_port: Optional[int]  # None → descriptor.default_observe_port
    topic_prefix: str
    device_name: str

    @classmethod
    def from_env(cls, index: int) -> 'ApplianceConfig':
        prefix = f'APPLIANCE_{index}_'
        klass = os.getenv(prefix + 'CLASS')
        if not klass:
            raise ValueError(f"{prefix}CLASS not set")
        ip = os.getenv(prefix + 'IP')
        if not ip:
            raise ValueError(f"{prefix}IP not set")
        port_env = os.getenv(prefix + 'OCF_PORT')
        port = int(port_env) if port_env else None
        topic = os.getenv(prefix + 'TOPIC') or f'samsung_{klass}'
        name = os.getenv(prefix + 'NAME') or f'Samsung {klass.title()}'
        return cls(
            index=index, klass=klass, ip=ip, ocf_port=port,
            topic_prefix=topic, device_name=name,
        )


def load_appliances() -> list[ApplianceConfig]:
    """Read APPLIANCE_COUNT and build the appliance list. At least one
    is required."""
    count = int(os.getenv('APPLIANCE_COUNT', '1'))
    if count < 1:
        raise ValueError("APPLIANCE_COUNT must be >= 1")
    return [ApplianceConfig.from_env(i + 1) for i in range(count)]
