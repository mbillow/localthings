"""Redact account/identity data from a raw resource tree before it leaves
the user's Home Assistant instance (diagnostics downloads, issue reports).

/device/0 dumps mix appliance state with genuinely sensitive data when
Bixby/voice is set up on the device: a Samsung account email, a Bixby
access token, a hashed device ID, WiFi/BLE MAC addresses, the serial
number, and otnDUID. This walks the whole tree and redacts any value whose
key matches a known-sensitive substring, regardless of which href it's
under — new device types will have unknown-shaped data we can't fully
enumerate in advance, so this errs on catching the field by name rather
than only redacting inside hrefs we already recognize.
"""

from __future__ import annotations

import re

REDACTED = "**REDACTED**"

_SENSITIVE_SUBSTRINGS = (
    "mac",
    "serial",
    "token",
    "login",
    "account",
    "email",
    "userid",
    "deviceid",
    "uuid",
    "duid",
    "tglogid",
    "password",
    "secret",
    # DevConf's x.com.samsung.ssolist: embedded JSON holding the owner's
    # Samsung account e-mail. Its inner keys aren't walked, so drop it whole.
    "ssolist",
    # /wirelessinfo/vs/0's connectedApSsid -- the owner's WiFi network name.
    # Not an account credential, which is why it slipped past the rules
    # above, but it is a location identifier: public wardriving databases map
    # SSIDs to street addresses. Eleven dumps in tests/fixtures reached this
    # repo carrying one before this rule existed.
    "ssid",
)

# Matched whole, not as substrings: these are bare one/two-letter keys too
# short for the substring rules above ('n' is a substring of very nearly
# everything). 'n' is /oic/d's free-text device name, which the owner sets
# from the SmartThings app and can carry a person's name -- the device-type
# signal we actually want from that resource is `rt`, which is not redacted.
#
# 'tnn'/'cd' are oic.r.wificonf's SSID and WiFi passphrase, which AV boards
# serve on a readable href.
#
# /oic/d's `di` and /oic/p's `pi` are deliberately not redacted: they're
# randomly-assigned per-unit UUIDs rather than account data, and they are
# what registry keys are minted from (issue #381), so blanking them hides
# the identity every entity in a report is named after -- which is exactly
# what made #381's first diagnostics download unable to answer it.
# x.com.samsung.rmd: the paired phone's name and model; laststatus and
# connectioninfo: provisioning ids and an on/off history.
_SENSITIVE_EXACT = frozenset(
    {
        "n",
        "tnn",
        "cd",
        "x.com.samsung.networkaudio.name",
        "x.com.samsung.rmd",
        "x.com.samsung.provisioning.sessionid",
        "x.com.samsung.provisioning.laststatus",
        "x.com.samsung.devicelog.connectioninfo",
    }
)

# Soundbar connection rows (soundFrom, lastConnections, networkInfo) carry a
# connectionType; their name is the source device's, often a phone named
# after its owner, and ip is a LAN address.
_CONNECTION_ROW_KEYS = frozenset({"name", "groupname", "ip"})

# Whole-segment matches (the whole key, or its tail after "." or "_"):
# UPnP's udn is a per-unit UUID, and sn/dsn are serial numbers (the TV's
# x.com.samsung.tv.sn, the soundbar's Alexa product_dsn). As substrings they
# show up inside harmless keys (autoLoudness and the like).
_SENSITIVE_SUFFIXES = ("udn", "sn", "dsn")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT:
        return True
    if any(lowered == s or lowered.endswith(("." + s, "_" + s)) for s in _SENSITIVE_SUFFIXES):
        return True
    return any(s in lowered for s in _SENSITIVE_SUBSTRINGS)


# oic.r.devconf's x.com.samsung.rsd embeds MACs in a JSON string under keys
# like 'bm', which is also an /oic/res link key, so match MACs by value shape.
_MAC_VALUE_RE = re.compile(r"\b(?:[0-9a-fA-F]{2}:){3,5}[0-9a-fA-F]{2}\b")


def redact_resources(resources):
    """Recursively redact sensitive values, matched by key name or by shape.

    Works on the shape produced by parse_device0_batch (dict[href, rep]) or
    any nested dict/list structure within a rep.
    """
    if isinstance(resources, dict):
        row = "connectionType" in resources
        return {
            key: (
                REDACTED
                if _is_sensitive_key(key) or (row and key.lower() in _CONNECTION_ROW_KEYS)
                else redact_resources(value)
            )
            for key, value in resources.items()
        }
    if isinstance(resources, list):
        return [redact_resources(item) for item in resources]
    if isinstance(resources, str):
        return _MAC_VALUE_RE.sub(REDACTED, resources)
    return resources
