#!/usr/bin/env bash
# Start a fresh Plone 6.2 site "plone" (admin/admin).
# Variant (arg 1):
#   classic (default) -> container plonex-s4-plone on 127.0.0.1:$PLONE_PORT.
#       The classic distribution with SETUP_CONTENT=false does NOT install
#       plone.restapi (anonymous then gets 401 "Missing 'plone.restapi: Use
#       REST API' permission"), so the profile is installed explicitly.
#   volto -> container plonex-s4-plone-volto on 127.0.0.1:$PLONE_VOLTO_PORT
#       (plone.volto distribution: blocks behaviour on Document).
set -euo pipefail
cd "$(dirname "$0")"; . ./common.sh
VARIANT=${1:-classic}
if [ "$VARIANT" = volto ]; then NAME=${PREFIX}-plone-volto; PORT=${PLONE_VOLTO_PORT}; DIST=volto
else NAME=${PREFIX}-plone; PORT=${PLONE_PORT}; DIST=classic; fi
run "
docker rm -f ${NAME} >/dev/null 2>&1 || true
docker run -d --name ${NAME} --restart unless-stopped \
  -p 127.0.0.1:${PORT}:8080 \
  -e SITE=plone -e DISTRIBUTION=${DIST} -e PROFILES=plone.restapi:default \
  -e SETUP_CONTENT=false -e TIMEZONE=UTC \
  plone/plone-backend:6.2 >/dev/null
for i in \$(seq 1 150); do
  if curl -sf -o /dev/null -H 'Accept: application/json' http://127.0.0.1:${PORT}/plone; then echo ${NAME} up on ${PORT}; exit 0; fi
  sleep 2
done
echo '${NAME} did not come up' >&2; docker logs --tail 50 ${NAME}; exit 1
"
