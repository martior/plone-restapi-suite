#!/usr/bin/env bash
# Forward the target ports from the docker host to localhost (background, idempotent).
set -euo pipefail
cd "$(dirname "$0")"; . ./common.sh
pkill -f "ssh -fNT -L ${PLONE_PORT}:" 2>/dev/null || true
ssh -fNT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L ${PLONE_PORT}:127.0.0.1:${PLONE_PORT} -L ${NICK_PORT}:127.0.0.1:${NICK_PORT} \
  -L ${PLONE_VOLTO_PORT}:127.0.0.1:${PLONE_VOLTO_PORT} -L ${NICK_PG_PORT}:127.0.0.1:${NICK_PG_PORT} "$DOCKER_HOST_SSH"
echo "tunnel: plone http://127.0.0.1:${PLONE_PORT}/plone  nick http://127.0.0.1:${NICK_PORT}"
