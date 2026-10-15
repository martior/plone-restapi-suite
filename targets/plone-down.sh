#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"; . ./common.sh
if [ "${1:-classic}" = volto ]; then run "docker rm -f ${PREFIX}-plone-volto || true"; else run "docker rm -f ${PREFIX}-plone || true"; fi
