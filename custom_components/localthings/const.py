DOMAIN = "localthings"

PLATFORMS = ["sensor", "binary_sensor", "switch", "number", "select", "button"]

CONF_HOST         = "host"
CONF_PORT         = "port"
CONF_CA_CERT_PEM  = "ca_cert_pem"
CONF_CA_KEY_PEM   = "ca_key_pem"
CONF_LEAF_CERT_PEM = "leaf_cert_pem"
CONF_LEAF_KEY_PEM  = "leaf_key_pem"

PROBE_PORTS = [49154, 49155]

ACTIVE_INTERVAL_S = 5.0
IDLE_INTERVAL_S   = 30.0
