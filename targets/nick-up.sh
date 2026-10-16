#!/usr/bin/env bash
# Start Postgres 18 + Nick (from a copy of vendor/nick) on 127.0.0.1:$NICK_PORT.
# Nick profiles: core + examplecontent (gives admin/admin with role Administrator).
# NICK_SRC can point at a patched copy; vendor/ itself is never modified.
# NICK_EXTRA_PROFILES: space separated profile directories, relative to targets/ (e.g.
# nick-profiles/restapi_suite_acquire), copied into the Nick copy and
# added to config.ts after examplecontent. Requires a remote or patched copy.
set -euo pipefail
cd "$(dirname "$0")"; . ./common.sh
NICK_SRC=${NICK_SRC:-$(cd ../../../vendor/nick && pwd)}
if [ -n "$DOCKER_HOST_SSH" ]; then
  ssh "$DOCKER_HOST_SSH" "mkdir -p ~/${REMOTE_DIR}/nick"
  rsync -a --delete --exclude node_modules --exclude .git --exclude var --exclude /config.ts \
    "$NICK_SRC"/ "$DOCKER_HOST_SSH:${REMOTE_DIR}/nick/"
  RDIR="\$HOME/${REMOTE_DIR}/nick"
  for p in ${NICK_EXTRA_PROFILES:-}; do
    rsync -a --delete "$p"/ "$DOCKER_HOST_SSH:${REMOTE_DIR}/nick/src/profiles/$(basename "$p")/"
  done
else
  for p in ${NICK_EXTRA_PROFILES:-}; do
    rm -rf "$NICK_SRC/src/profiles/$(basename "$p")" && cp -R "$p" "$NICK_SRC/src/profiles/$(basename "$p")"
  done
  RDIR="$NICK_SRC"
fi
EXTRA_NAMES=$(for p in ${NICK_EXTRA_PROFILES:-}; do basename "$p"; done | tr '\n' ' ')
run "
set -e
docker network inspect ${NET} >/dev/null 2>&1 || docker network create ${NET} >/dev/null
docker rm -f ${PREFIX}-nick ${PREFIX}-nick-pg >/dev/null 2>&1 || true
docker volume rm ${PREFIX}-nick-pgdata >/dev/null 2>&1 || true
docker run -d --name ${PREFIX}-nick-pg --restart unless-stopped --network ${NET} \
  -p 127.0.0.1:${NICK_PG_PORT}:5432 \
  -e POSTGRES_USER=nick -e POSTGRES_PASSWORD=nick -e POSTGRES_DB=nick \
  -v ${PREFIX}-nick-pgdata:/var/lib/postgresql \
  postgres:18 >/dev/null
for i in \$(seq 1 60); do docker exec ${PREFIX}-nick-pg pg_isready -U nick >/dev/null 2>&1 && break; sleep 1; done
cd $RDIR && cp config.ts.sample config.ts
for p in ${EXTRA_NAMES}; do sed -i \"s#'@plone/nick:examplecontent',#&\\n    '@plone/nick:\$p',#\" config.ts; done
docker volume create ${PREFIX}-nick-modules >/dev/null
docker volume create ${PREFIX}-nick-pnpm-store >/dev/null
docker run -d --name ${PREFIX}-nick --restart unless-stopped --network ${NET} \
  -p 127.0.0.1:${NICK_PORT}:8080 \
  -v $RDIR:/app -v ${PREFIX}-nick-modules:/app/node_modules -v ${PREFIX}-nick-pnpm-store:/pnpm-store \
  -w /app \
  -e DB_HOST=${PREFIX}-nick-pg -e DB_PORT=5432 -e DB_USER=nick -e DB_PASSWORD=nick -e DB_NAME=nick \
  -e API_RATE_LIMIT=1000000 -e AUTH_RATE_LIMIT=1000000 -e CI=true \
  node:26 sh -c 'npm i -g corepack@latest >/dev/null && corepack enable && \
     pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile && \
     pnpm migrate && pnpm seed && exec ./node_modules/.bin/tsx src/server.ts' >/dev/null
for i in \$(seq 1 300); do
  if curl -sf -o /dev/null -H 'Accept: application/json' http://127.0.0.1:${NICK_PORT}/; then echo nick up; exit 0; fi
  if [ \"\$(docker inspect -f '{{.State.Running}}' ${PREFIX}-nick)\" != true ]; then break; fi
  sleep 2
done
echo 'nick did not come up' >&2; docker logs --tail 80 ${PREFIX}-nick; exit 1
"
