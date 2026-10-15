#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"; . ./common.sh
run "docker rm -f ${PREFIX}-nick ${PREFIX}-nick-pg || true; docker volume rm ${PREFIX}-nick-pgdata || true"
