FROM python:3.11-slim

WORKDIR /app

# Python deps first so layer cache survives code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY main.py .
COPY samsung_appliance/ ./samsung_appliance/

# /config holds the ab0b0ac4 client cert + key. Mount from the host so
# secrets aren't baked into the image.
RUN mkdir -p /config

# Unbuffered stdout so docker logs is live
ENV PYTHONUNBUFFERED=1

# Defaults — override in .env or `docker run -e …`. Per-appliance
# keys (APPLIANCE_COUNT, APPLIANCE_<n>_*) have no universal default
# and must be set in .env.
ENV CERT_PATH=/config/ab0b0ac4_fullchain.pem \
    KEY_PATH=/config/ab0b0ac4.key \
    HA_DISCOVERY_PREFIX=homeassistant \
    HEALTH_INTERVAL_S=60 \
    HEARTBEAT_INTERVAL_S=600

# No port — bridge is outbound-only (DTLS UDP to appliance, MQTT to broker).

CMD ["python", "main.py"]
