#!/bin/bash
# Sync source + .env to the remote and rebuild the container.
#
# Two host paths are used:
#   REMOTE_DIR  — compose project (source code, .env, docker-compose.yml)
#                 Convention: /mnt/user/compose/samsung-bridge/
#   APPDATA_DIR — bind-mount source for /config inside the container
#                 (ab0b0ac4 client cert + key live here).
#                 Convention: /mnt/user/appdata/samsung-bridge/
#
# The remote must already have the certs in $APPDATA_DIR. Run once
# before the first deploy:
#
#   source .env
#   ssh "$SSH_HOST" mkdir -p "$APPDATA_DIR"
#   scp certs/ab0b0ac4_fullchain.pem certs/ab0b0ac4.key \
#       "$SSH_HOST:$APPDATA_DIR/"
#
# Subsequent deploys (this script) ship source code + .env only; the
# certs in $APPDATA_DIR are preserved.
set -e

if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example to .env and configure it."
    exit 1
fi

# Pull only the keys deploy.sh actually needs, without sourcing .env.
# Sourcing would tokenize unquoted spaces in values (e.g.
# `APPLIANCE_1_NAME=Samsung Dryer`) as shell commands.
get_env() {
    grep -E "^${1}=" .env | head -1 | cut -d= -f2-
}
SSH_HOST=$(get_env SSH_HOST)
REMOTE_DIR=$(get_env REMOTE_DIR)
APPDATA_DIR=$(get_env APPDATA_DIR)

: "${SSH_HOST:?SSH_HOST not set in .env}"
: "${REMOTE_DIR:?REMOTE_DIR not set in .env}"
: "${APPDATA_DIR:?APPDATA_DIR not set in .env}"

echo "Deploying to ${SSH_HOST}:${REMOTE_DIR}…"
ssh "${SSH_HOST}" mkdir -p "${REMOTE_DIR}" "${APPDATA_DIR}"

# Source code — explicit allowlist instead of an excludelist. Anything
# else in the repo (research files, certs, logs, the .git dir) stays
# local.
COPYFILE_DISABLE=1 tar cz \
    main.py \
    samsung_appliance/ \
    Dockerfile \
    docker-compose.yml \
    requirements.txt \
    deploy.sh \
    README.md \
    .env.example \
    .gitignore \
  | ssh "${SSH_HOST}" "cd ${REMOTE_DIR} && tar xz && find . -name '._*' -delete"

# Ship .env separately and lock it down on the remote.
scp .env "${SSH_HOST}:${REMOTE_DIR}/.env"
ssh "${SSH_HOST}" "chmod 600 ${REMOTE_DIR}/.env"

# Verify certs are present on the remote — they have to be uploaded
# once before the first build.
if ! ssh "${SSH_HOST}" "test -s ${APPDATA_DIR}/ab0b0ac4_fullchain.pem && test -s ${APPDATA_DIR}/ab0b0ac4.key"; then
    echo
    echo "WARNING: ${APPDATA_DIR}/ab0b0ac4_fullchain.pem and ab0b0ac4.key not"
    echo "found on the remote. The container will start but fail to"
    echo "connect to the appliance until you upload them, e.g.:"
    echo "  ssh ${SSH_HOST} mkdir -p ${APPDATA_DIR}"
    echo "  scp certs/ab0b0ac4_fullchain.pem certs/ab0b0ac4.key ${SSH_HOST}:${APPDATA_DIR}/"
    echo
fi

echo "Rebuilding container…"
ssh "${SSH_HOST}" "cd ${REMOTE_DIR} && docker compose up -d --build"

echo "Done."
echo "Logs:  ssh ${SSH_HOST} 'cd ${REMOTE_DIR} && docker compose logs -f'"
