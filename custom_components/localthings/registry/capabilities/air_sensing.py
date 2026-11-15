"""Partial writes and packed skip windows shared by air-sensing devices."""

import datetime


def periodic_sensing_write(payload, rep, href=None):
    # Master on/off; leaves autoExeState alone so the configured action
    # survives the feature being toggled off -- the select can't do that,
    # since every option write sets an action too.
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingActivationState": ("On" if payload == "On" else "Off")
    }


def skip_status_write(payload, rep, href=None):
    return ["airlevelcheck", "vs", "0"], {
        "x.com.samsung.da.periodicSensingSkipStatus": ("On" if payload == "On" else "Off")
    }


# Each time write preserves the other half of the device's HHMMHHMM window.
def skip_time_read(part):
    def _read(value):
        raw = str(value or "")
        chunk = raw[0:4] if part == "start" else raw[4:8]
        if len(chunk) == 4 and chunk.isdigit():
            try:
                return datetime.time(int(chunk[:2]), int(chunk[2:]))
            except ValueError:
                return None
        return None

    return _read


def _skip_half(raw, part):
    """The half this write isn't setting, normalized. An unparseable half
    becomes '0000' rather than carrying a malformed value back to the
    device."""
    chunk = (str(raw or "") + "00000000")[:8]
    other = chunk[4:8] if part == "start" else chunk[0:4]
    return other if skip_time_read("end" if part == "start" else "start")(chunk) else "0000"


def skip_time_write(part):
    def _write(value, rep, href=None):
        raw = rep.get("x.com.samsung.da.periodicSensingSkipTime", "")
        hhmm = f"{value.hour:02d}{value.minute:02d}"
        other = _skip_half(raw, part)
        new = hhmm + other if part == "start" else other + hhmm
        return ["airlevelcheck", "vs", "0"], {"x.com.samsung.da.periodicSensingSkipTime": new}

    return _write
