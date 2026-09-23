DOMAIN = "localthings"

PLATFORMS = [
    "sensor",
    "binary_sensor",
    "switch",
    "number",
    "select",
    "button",
    "time",
    "climate",
    "fan",
    "water_heater",
]

CONF_HOST = "host"
CONF_PORT = "port"

# Which transport this entry's appliance speaks (issue #168). Absent on
# every entry created before a second one existed, which is why the DTLS
# value is also the default everywhere it is read.
CONF_TRANSPORT = "transport"
TRANSPORT_DTLS = "dtls"
TRANSPORT_LEGACY_HTTP = "legacy_http"

# The legacy 8888 bridge needs one credential the CoAP path does not: a
# device token, which only the appliance can issue (see
# legacy_http_token.py). Stored on the entry beside the leaf.
CONF_DEVICE_TOKEN = "device_token"

# Which envelope table that appliance's responses are read with -- the
# `description` it reports for itself, e.g. 'TP6X_WASHER'. Recorded even
# when it names no table this integration knows, so a diagnostics dump
# from an unmapped family says which one it was.
CONF_LEGACY_FAMILY = "legacy_family"

CONF_CA_CERT_PEM = "ca_cert_pem"
CONF_CA_KEY_PEM = "ca_key_pem"
CONF_LEAF_CERT_PEM = "leaf_cert_pem"
CONF_LEAF_KEY_PEM = "leaf_key_pem"

# Device identity, resolved once by the config flow's probe and persisted
# on the entry (issue #236) -- what the coordinator mints registry keys
# from at __init__ time, before any poll has happened. Without them,
# anything registering before the first poll (e.g. the connection-mode
# sensor) got keyed on the IP address permanently.
#
# CONF_SERIAL is the resolved serial (registry.identity.resolve_serial's
# output, the host itself for a placeholder-serial board -- issues
# #83/#189), so it matches what _run_discovery computes on the first poll.
CONF_SERIAL = "serial"
# What this entry's devices and entities are keyed on -- normally the OCF
# device UUID (issue #381). CONF_SERIAL stays alongside it as the pre-v4
# key to re-key from, and as what corroborates a later change of UUID.
# Absent until the first live poll, since only the device can report it.
CONF_DEVICE_KEY = "device_key"
# The OCF device UUID this appliance actually proved over an authenticated
# session -- /oic/d's `di` and nothing else. Deliberately separate from
# CONF_DEVICE_KEY: that is a registry key resolved through a fallback chain
# (`di` -> /oic/p's `pi` -> serialNum -> host), so it is not proof of the OCF
# identity and can hold a platform UUID, a serial, or an address. Absent
# until a live authenticated read reports a usable `di`, and never used to
# key devices or entities.
CONF_OCF_DEVICE_ID = "ocf_device_id"
# The appliance's own WiFi MAC (registry.identity.resolve_mac), stored so a
# DHCP sighting can be matched back to this entry and its host followed when
# the lease moves (issue #469). Absent on boards that don't report
# /wirelessinfo/vs/0, and on entries whose last poll predates this key.
CONF_MAC = "mac"
CONF_MODEL = "model"
CONF_MANUFACTURER = "manufacturer"
CONF_DEVICE_TYPE = "device_type"

# entry.data key: modes this device reported itself in but never advertised
# in the same resource's supportedModes (issue #327). Stored on the entry
# rather than kept in memory so a mode the device only names while it is
# active survives a restart -- see learned.py. Shape:
# {actual_href: [code, ...]}.
CONF_LEARNED_MODES = "learned_modes"

# Options-flow key: whether learned modes are remembered and offered.
# Defaults to on; turning it off stops both halves at once (nothing new is
# learned, nothing already learned is offered) without discarding what was
# already remembered -- the options flow's reset step does that.
CONF_LEARN_MODES = "learn_device_modes"
DEFAULT_LEARN_MODES = True

# entry.data key: cloud "Download" programs discovered on a laundry device
# (issue #342). Same rationale as CONF_LEARNED_MODES -- a program's full
# replay payload is only ever visible while the device happens to be sitting
# on it, so it has to survive a restart -- but a richer shape, because a
# cloud program also needs a user-supplied name and the device's own
# Download course code. See cloudcourse.py, which owns the shape. Shape:
# {"download_course": "87"|null, "slots": {slot: {"blob": ..., "name": ...}}}
CONF_CLOUD_COURSES = "cloud_courses"

# Options-flow key: whether downloaded programs are offered as selectable
# cycles and nagged about via the "not set up yet" Repair (issue #364).
# Defaults to on. Unlike CONF_LEARN_MODES this does not also stop passive
# observation -- a device that merely *advertises* download slots without
# the owner ever meaning to use them (SmartThings appears to seed one from
# the cloud automatically, per #364's reporters) is exactly the case this
# exists for, and turning it off is the fix. Guided/manual setup stay
# reachable and still record what they see either way: they are a deliberate
# per-session action, not the passive background behavior this silences, and
# leaving them working means flipping the option back on immediately surfaces
# anything set up in the meantime instead of asking the user to redo it. See
# coordinator.cloud_courses_enabled for exactly what it gates.
CONF_CLOUD_COURSES_ENABLED = "cloud_courses_enabled"
DEFAULT_CLOUD_COURSES_ENABLED = True

# Options-flow key (entry.options, not entry.data): lets a user override
# the device-wide remote-control-off write block for a specific device
# (issue #54). Some devices accept certain writes even while reporting
# remote control off (e.g. a washer's default detergent dosing), so the
# blanket-block assumption doesn't hold everywhere. Defaults to False
# (block stays on).
CONF_BYPASS_REMOTE_CONTROL = "bypass_remote_control_lock"

# Options-flow key: minimum change (in minutes) required before a
# hysteresis-gated timestamp sensor (currently just finish_time) reports a
# new value. Devices commonly revise their remaining-time estimate by a
# minute or two throughout a cycle, and finish_time = now() + remaining
# drifts with the poll interval between revisions -- both push a fresh
# state far more often than the estimate is meaningfully different. 0
# disables the gate.
CONF_FINISH_TIME_HYSTERESIS_MINUTES = "finish_time_hysteresis_minutes"
DEFAULT_FINISH_TIME_HYSTERESIS_MINUTES = 3

# The DTLS/CoAP local API binds somewhere in this ephemeral range,
# depending on firmware (newer builds answer on 49154/49155, older ones as
# low as 49153) -- swept for a live UDP port before the expensive DTLS
# handshake.
PROBE_PORT_RANGE = list(range(49152, 49161))

# Ports we've historically seen complete a DTLS handshake; tried first when
# more than one port in the range looks live.
PREFERRED_PROBE_PORTS = [49154, 49155]

# Per-port timeout for the cheap UDP liveness sweep. Closed ports return an
# ICMP port-unreachable almost immediately; a live-but-silent port is only
# detected by this timeout elapsing, so keep it short. Only reached as the
# fallback for when the ClientHello probe below confirms nothing.
LIVENESS_PROBE_TIMEOUT_S = 1.5

# Per-port budget for the DTLS ClientHello probe (smartthings-local >=
# 0.1.2), the primary port-detection gate. A real server answers with a
# HelloVerifyRequest in ~1 RTT; the budget only bounds how long a silent
# port takes to give up. 3s covers two retransmits on a slow LAN.
CLIENTHELLO_PROBE_TIMEOUT_S = 3.0
CLIENTHELLO_PROBE_RETRIES = 2

# Deadline for the blockwise /device/0 GET during the config-flow probe.
# The slowest device observed returns a full dump in ~8s.
PROBE_GET_TIMEOUT_S = 10.0

# Plaintext CoAP ports to ask, in order, for the device's own secure-port
# advertisement. 5683 is IoTivity classic's multicast plaintext socket, bound
# to INADDR_ANY, so a unicast datagram lands on it on every board in this
# family (issue #482). The device's unicast socket is kernel-assigned and
# moves, so the advertisement is the only thing that names it -- and the only
# thing that reaches a port outside PROBE_PORT_RANGE. 49153 is asked only
# when 5683 advertised nothing, as one extra datagram for a board that does
# not answer the standard port -- asking both at once corrupts the device's
# per-peer transfer state (see _discover_advertised_ports).
PLAINTEXT_DISCOVERY_PORTS = [5683, 49153]

# Budget for the advertisement lookup, and for the two identity reads behind
# it. Retries are load-bearing and the budget barely is: at retries=1 a lost
# datagram cost one run in five on the dishwasher, while retries=2 answered
# 5/5 at every budget from 1.5s to 3.0s. A hit lands in ~0.1-0.6s; the budget
# only bounds how long a silent host takes to give up.
PLAINTEXT_DISCOVERY_TIMEOUT_S = 2.5
PLAINTEXT_READ_TIMEOUT_S = 1.5
PLAINTEXT_DISCOVERY_RETRIES = 2

# IoTivity classic's multicast secure socket, bound to INADDR_ANY, so a
# ClientHello to it draws a first flight on any board in this family (issue
# #482). It does serve a full session -- measured byte-identical to the
# unicast port on two boards, 2026-09-21 -- but the advertised unicast port
# is still the device's own answer about itself and stays canonical; 5684
# is dialled last as a fallback rescue for a board whose unicast port falls
# outside PROBE_PORT_RANGE.
MULTICAST_SECURE_PORT = 5684

# The legacy HTTPS bridge (issue #168). A board that serves it has no CoAP
# server at all, so one TCP connect discriminates the two lineages.
LEGACY_HTTP_PORT = 8888
LEGACY_HTTP_PROBE_TIMEOUT_S = 2.0

# Base for the local (client-side) DTLS source port, distinct from the
# destination probe ports above -- see coordinator._local_source_port for
# why a fixed per-device source port matters. Above Linux's default
# ip_local_port_range (32768-60999): the previous 49700 sat inside it, so
# an unrelated process could hold the port and the bind failed EADDRINUSE
# on a host-networked install (issue #486). Requires smartthings-local >= 0.1.1.
DTLS_LOCAL_PORT_BASE = 61000

SUMMARY_INTERVAL_S = 30.0

DEVICE_SUPPORT_ISSUE_URL = (
    "https://github.com/mbillow/localthings/issues/new?template=device-support.yml"
)

# Service names (services.py), shared with config_flow.py so the
# options-flow debug panel calls the exact same service a user could call
# from an automation (issue #300) -- one code path performs a raw write.
SERVICE_WRITE_RESOURCE = "write_resource"
SERVICE_READ_RESOURCE = "read_resource"
