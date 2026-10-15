# Shared settings for the target scripts. Everything on the docker host is
# prefixed plonex-s4- and bound to 127.0.0.1 only.
: "${DOCKER_HOST_SSH=}"               # ssh alias of a remote docker host (empty = local docker)
: "${PLONE_PORT:=18640}"
: "${NICK_PORT:=18641}"
: "${PLONE_VOLTO_PORT:=18643}"
: "${NICK_PG_PORT:=18642}"            # only for debugging (psql via tunnel)
: "${REMOTE_DIR:=plonex-s4}"          # relative to remote $HOME
PREFIX=plonex-s4
NET=${PREFIX}-net

run() {  # run a shell snippet on the docker host
  if [ -n "$DOCKER_HOST_SSH" ]; then ssh "$DOCKER_HOST_SSH" "bash -lc $(printf %q "$1")"; else bash -lc "$1"; fi
}
