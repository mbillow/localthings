DOMAIN = "localthings"

PLATFORMS = ["sensor", "binary_sensor", "switch", "number", "select", "button", "time"]

CONF_HOST         = "host"
CONF_PORT         = "port"
CONF_CA_CERT_PEM  = "ca_cert_pem"
CONF_CA_KEY_PEM   = "ca_key_pem"
CONF_LEAF_CERT_PEM = "leaf_cert_pem"
CONF_LEAF_KEY_PEM  = "leaf_key_pem"

# The DTLS/CoAP local API binds somewhere in this ephemeral range; which port
# depends on firmware. Newer builds answer on 49154/49155, but older ones have
# been seen as low as 49153, so we sweep the whole range for a live UDP port
# before attempting the (expensive) DTLS handshake.
PROBE_PORT_RANGE = list(range(49152, 49161))

# Ports we've historically seen complete a DTLS handshake. When more than one
# port in the range looks live, these are tried first.
PREFERRED_PROBE_PORTS = [49154, 49155]

# Per-port timeout for the cheap UDP liveness sweep. Closed ports return an
# ICMP port-unreachable almost immediately; a live-but-silent port is only
# detected by this timeout elapsing, so keep it short.
LIVENESS_PROBE_TIMEOUT_S = 1.5

# Deadline for the blockwise /device/0 GET during the config-flow probe. The
# slowest device observed returns a full dump in ~8s, so 10s leaves headroom
# without stalling setup; it matches the per-resource read timeout elsewhere.
PROBE_GET_TIMEOUT_S = 10.0

SUMMARY_INTERVAL_S = 30.0

DEVICE_SUPPORT_ISSUE_URL = (
    "https://github.com/mbillow/localthings/issues/new?template=device-support.yml"
)
