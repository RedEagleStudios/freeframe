# Stubs for every side effect of `freeframe update`, `configure`, `status`,
# `logs` and `restart`. update.bats, configure.bats and manage.bats source
# this file after the script, in a fresh bash, so these definitions replace
# the real wrappers. Each call is appended to $CALLS, one line per call. The
# STUB_* environment variables are the knobs the tests turn.
#
# Downloads come from $STUB_REMOTE, a folder laid out like the repository at
# the channel's ref (the real fetch, through FF_FETCH_FROM_DIR). Nothing here
# reaches the network, Docker, PostgreSQL or systemd.

_stub_log() { printf '%s\n' "$*" >>"$CALLS"; }

ff_is_root() { [[ -z ${STUB_NOT_ROOT:-} ]]; }
ff_sleep() { _stub_log "sleep $1"; }
_ff_free_mb() { printf '%s\n' "${STUB_FREE_MB:-80000}"; }

public_ip() {
  [[ -n ${STUB_PUBLIC_IP-203.0.113.7} ]] || return 1
  printf '%s\n' "${STUB_PUBLIC_IP-203.0.113.7}"
}
local_ip() { printf '%s\n' "${STUB_LOCAL_IP:-203.0.113.7}"; }

eval "_real_fetch() $(declare -f fetch | tail -n +2)"
fetch() {
  _stub_log "fetch $1 $2"
  [[ -z ${STUB_FETCH_FAIL:-} || $2 != "$STUB_FETCH_FAIL" ]] || return 1
  FF_FETCH_FROM_DIR=$STUB_REMOTE _real_fetch "$@"
}

# The self-update's re-exec, followed in this process (the real one would
# start the downloaded script without these stubs). STUB_REAL_REEXEC keeps
# the real one.
if [[ -z ${STUB_REAL_REEXEC:-} ]]; then
  _ff_reexec() {
    _stub_log "reexec $*"
    shift
    export FF_SELF_UPDATED=1
    main "$@"
    exit $?
  }
fi

install_backup_timer() {
  _stub_log install_backup_timer
  [[ -z ${STUB_TIMER_FAIL:-} ]]
}

systemctl() {
  _stub_log "systemctl $*"
  case "$*" in
    *NextElapseUSecRealtime*) printf '%s\n' "${STUB_TIMER_NEXT-Tue 2026-10-27 03:07:12 UTC}" ;;
    *ActiveState*) printf '%s\n' "${STUB_TIMER_STATE:-active}" ;;
  esac
  return 0
}

dns_points_here() {
  local n=0 f="$STUB_DIR/dns-count"
  [[ -f $f ]] && n=$(<"$f")
  n=$((n + 1))
  printf '%s' "$n" >"$f"
  _stub_log "dns_points_here $1 $2"
  ((n > ${STUB_DNS_FAILS:-0}))
}

probe_health() {
  _stub_log "probe_health $*"
  [[ -z ${STUB_HEALTH_FAIL:-} ]]
}

cert_issued() {
  _stub_log "cert_issued $1"
  [[ -z ${STUB_CERT_FAIL:-} ]]
}

cert_expiry() {
  _stub_log "cert_expiry $1"
  [[ -z ${STUB_CERT_FAIL:-} ]] || return 1
  printf '%s\n' "${STUB_CERT_END:-Jan 24 10:00:00 2027 GMT}"
}

remote_version() {
  _stub_log "remote_version $1"
  [[ -z ${STUB_NET_DOWN:-} ]] || return 1
  printf '%s\n' "${STUB_REMOTE_VERSION:-1.21.0}"
}

# docker compose for this install.
dc() {
  _stub_log "dc $*"
  case "$*" in
    "config --services")
      [[ -z ${STUB_DOCKER_DOWN:-} ]] || return 1
      printf '%s\n' traefik postgres redis api worker email_worker maintenance_worker beat web minio minio-init
      ;;
    "ps "*)
      [[ -z ${STUB_DOCKER_DOWN:-} ]] || return 1
      printf "%s|%s|%s\n" traefik running "Up 2 hours" postgres running "Up 2 hours (healthy)" \
        api running "Up 2 hours (healthy)" worker exited "Exited (1) 5 minutes ago"
      ;;
    "exec -T postgres pg_isready "*) [[ -z ${STUB_PG_DOWN:-} ]] ;;
    "exec -T postgres pg_dump "*)
      [[ -z ${STUB_PG_DOWN:-} ]] || return 1
      printf 'PGDMP current database\n'
      ;;
    *"localhost:8000/health"*) [[ -z ${STUB_API_DOWN:-} ]] ;;
    pull*) [[ -z ${STUB_PULL_FAIL:-} ]] ;;
    "up -d"*) [[ -z ${STUB_UP_FAIL:-} ]] ;;
    "logs "*) printf 'api-1  | started\n' ;;
    *) return 0 ;;
  esac
}

# derive_env is real; wrap it so the call shows up in $CALLS.
eval "_real_derive_env() $(declare -f derive_env | tail -n +2)"
derive_env() {
  _stub_log derive_env
  _real_derive_env "$@"
}
