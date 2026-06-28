"""Read device identity from standard OCF resources (/oic/p, /oic/d)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cbor2


@dataclass(frozen=True)
class DeviceIdentity:
    manufacturer: str
    model: str
    name: str
    serial: Optional[str]


def _get(sess, path) -> dict:
    try:
        code, pl = sess.get(path, timeout=10.0)
        if code == 0x45 and pl:
            body = cbor2.loads(pl)
            return body if isinstance(body, dict) else {}
    except Exception:
        pass
    return {}


def read_identity(sess, serial: Optional[str]) -> DeviceIdentity:
    p = _get(sess, ['oic', 'p'])
    d = _get(sess, ['oic', 'd'])
    return DeviceIdentity(
        manufacturer=p.get('mnmn') or 'Samsung',
        model=p.get('mnmo') or '',
        name=d.get('n') or '',
        serial=serial,
    )
