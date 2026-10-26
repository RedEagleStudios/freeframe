# Stubs for every side effect of `freeframe install`. install.bats sources
# this file after the script, in a fresh bash, so these definitions replace
# the real wrappers. Each call is appended to $CALLS, one line per call, so a
# test can check what ran and in which order. The STUB_* environment
# variables are the knobs the tests turn.
#
# Nothing here reaches the network, Docker, apt, systemd or the firewall.

_stub_log() { printf '%s\n' "$*" >>"$CALLS"; }

ff_is_root() { [[ -z ${STUB_NOT_ROOT:-} ]]; }
machine_arch() { printf '%s\n' "${STUB_ARCH:-x86_64}"; }

detect_foreign_install() {
  _stub_log detect_foreign_install
  return "${STUB_FOREIGN:-1}"
}

docker_status() {
  _stub_log docker_status
  printf '%s\n' "${STUB_DOCKER:-none}"
}

install_docker() {
  _stub_log install_docker
  STUB_DOCKER=ok
}

run_apt() { _stub_log "run_apt $*"; }

public_ip() {
  [[ -n ${STUB_PUBLIC_IP-203.0.113.7} ]] || return 1
  printf '%s\n' "${STUB_PUBLIC_IP-203.0.113.7}"
}
local_ip() { printf '%s\n' "${STUB_LOCAL_IP:-203.0.113.7}"; }

net_reachable() {
  _stub_log "net_reachable $1"
  [[ -z ${STUB_NET_DOWN:-} || $1 != *"$STUB_NET_DOWN"* ]]
}

_ff_free_mb() { printf '%s\n' "${STUB_FREE_MB:-80000}"; }

# ss -Hltnp: STUB_SS holds the listening lines. systemctl clears it when
# STUB_SS_CLEARS is set (the program holding the port was stopped).
ss() {
  [[ -n ${STUB_SS:-} ]] && printf '%s\n' "$STUB_SS"
  return 0
}
_ff_unit_of_pid() { printf '%s\n' "${STUB_UNIT:-}"; }
systemctl() {
  _stub_log "systemctl $*"
  # STUB_SYSTEMCTL_FAIL: stopping a program fails (it keeps the port).
  [[ -z ${STUB_SYSTEMCTL_FAIL:-} ]] || return 1
  [[ -n ${STUB_SS_CLEARS:-} ]] && STUB_SS=""
  return 0
}

swap_present() { [[ -n ${STUB_SWAP:-} ]]; }
add_swap() { _stub_log add_swap; }
ufw_active() { [[ -n ${STUB_UFW_ACTIVE:-} ]]; }
ufw() { _stub_log "ufw $*"; }

dns_points_here() {
  local n=0 f="$STUB_DIR/dns-count"
  [[ -f $f ]] && n=$(<"$f")
  n=$((n + 1))
  printf '%s' "$n" >"$f"
  _stub_log "dns_points_here $1 $2"
  ((n > ${STUB_DNS_FAILS:-0}))
}

fetch() {
  _stub_log "fetch $1 $2"
  [[ -n ${STUB_FETCH_FAIL:-} && $2 == "$STUB_FETCH_FAIL" ]] && return 1
  printf 'services: {}\n' >"$3"
}

ff_sleep() { _stub_log "sleep $1"; }

install_backup_timer() {
  _stub_log install_backup_timer
  [[ -z ${STUB_TIMER_FAIL:-} ]]
}

probe_health() {
  _stub_log "probe_health $*"
  [[ -z ${STUB_HEALTH_FAIL:-} ]]
}

cert_issued() {
  _stub_log "cert_issued $1"
  [[ -z ${STUB_CERT_FAIL:-} ]]
}

# docker compose for this install.
dc() {
  _stub_log "dc $*"
  case "$*" in
    "config --services")
      if [[ -n ${STUB_SERVICES+x} ]]; then
        [[ -z $STUB_SERVICES ]] || printf '%s\n' $STUB_SERVICES
        return 0
      fi
      printf '%s\n' traefik postgres redis api worker email_worker maintenance_worker beat web
      if [[ $(env_get "$(ff_root)/.env.prod" FREEFRAME_STORAGE) == bundled ]]; then
        printf '%s\n' minio minio-init
      fi
      ;;
    *create-superadmin*)
      local body='{"message":"Superadmin created","access_token":"tok-SECRET"}'
      cat >"$STUB_DIR/superadmin.json"
      # STUB_ADMIN_EXEC_FAIL: docker compose exec itself fails (no HTTP code).
      [[ -z ${STUB_ADMIN_EXEC_FAIL:-} ]] || return 1
      printf '%s\n%s' "${STUB_ADMIN_BODY:-$body}" "${STUB_ADMIN_CODE:-201}"
      ;;
    "up -d --no-build traefik") [[ -z ${STUB_TRAEFIK_FAIL:-} ]] ;;
    *"localhost:8000/health"*) [[ -z ${STUB_API_DOWN:-} ]] ;;
    # Our own containers from an earlier attempt (STUB_OURS_RUNNING).
    # `stop traefik` stops it, unless STUB_TRAEFIK_STUCK.
    "ps -q traefik")
      [[ -n ${STUB_OURS_RUNNING:-} && ! -e $STUB_DIR/traefik-stopped ]] || return 0
      echo 0123456789ab
      ;;
    "ps -q "*) [[ -z ${STUB_OURS_RUNNING:-} ]] || echo 0123456789ab ;;
    "stop traefik") [[ -n ${STUB_TRAEFIK_STUCK:-} ]] || : >"$STUB_DIR/traefik-stopped" ;;
    pull*) [[ -z ${STUB_PULL_FAIL:-} ]] ;;
    *) return 0 ;;
  esac
}

# derive_env is real; wrap it so the call shows up in $CALLS.
eval "_real_derive_env() $(declare -f derive_env | tail -n +2)"
derive_env() {
  _stub_log derive_env
  # STUB_DERIVE_FAIL: the settings cannot be completed.
  [[ -z ${STUB_DERIVE_FAIL:-} ]] || return 1
  _real_derive_env "$@"
}
