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

# A ref with its own folder ($STUB_REMOTE_REFS/REF) is served from there;
# every other ref from $STUB_REMOTE. STUB_FETCH_FAIL_REF fails a whole ref.
eval "_real_fetch() $(declare -f fetch | tail -n +2)"
fetch() {
  _stub_log "fetch $1 $2"
  [[ -z ${STUB_FETCH_FAIL:-} || $2 != "$STUB_FETCH_FAIL" ]] || return 1
  [[ -z ${STUB_FETCH_FAIL_REF:-} || $1 != "$STUB_FETCH_FAIL_REF" ]] || return 1
  if [[ -n ${STUB_REMOTE_REFS:-} && -d $STUB_REMOTE_REFS/$1 ]]; then
    FF_FETCH_FROM_DIR=$STUB_REMOTE_REFS/$1 _real_fetch "$@"
  else
    FF_FETCH_FROM_DIR=$STUB_REMOTE _real_fetch "$@"
  fi
}

# _stub_hang NAME: for the signal tests. Note this process group (the test
# signals all of it, as Ctrl-C and a closed SSH session do), say the long
# step has started, and wait.
_stub_hang() {
  local -a st
  read -r -a st </proc/$BASHPID/stat
  printf '%s' "${st[4]}" >"$STUB_DIR/pgid"
  : >"$STUB_DIR/hanging-$1"
  sleep 30
}

# STUB_UNDO_SET_HANG: hang right after the update record is written.
if [[ -n ${STUB_UNDO_SET_HANG:-} ]]; then
  eval "_real_update_undo_set() $(declare -f _update_undo_set | tail -n +2)"
  _update_undo_set() {
    _real_update_undo_set "$@" || return 1
    _stub_hang undo-set
  }
fi

# STUB_PUTBACK_FAIL: the setup files from before cannot be put back.
if [[ -n ${STUB_PUTBACK_FAIL:-} ]]; then
  _update_put_back() {
    _stub_log _update_put_back
    return 1
  }
fi

# STUB_SAY_FAIL=TEXT: printing a line containing TEXT fails, an unexpected
# failure in the middle of the flow (the ERR trap's case).
if [[ -n ${STUB_SAY_FAIL:-} ]]; then
  eval "_real_say() $(declare -f say | tail -n +2)"
  say() {
    if [[ $* == *"$STUB_SAY_FAIL"* ]]; then return 1; fi
    _real_say "$@"
  }
fi

# docker itself (rollback stops by project label when the compose files in
# place cannot be read).
docker() {
  _stub_log "docker $*"
  case "$*" in
    "ps "*) printf '%s\n' 'c1|api' 'c2|postgres' 'c3|traefik' 'c4|worker' ;;
  esac
  return 0
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

# STUB_TIMER_HANG: refreshing the timer hangs (a signal after the update
# succeeded).
install_backup_timer() {
  _stub_log install_backup_timer
  [[ -z ${STUB_TIMER_HANG:-} ]] || _stub_hang timer
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
      # STUB_CONFIG_FAIL_NEW: the new release's compose files do not parse.
      if [[ -n ${STUB_CONFIG_FAIL_NEW:-} ]] && grep -q 1.22.0 "$FF_ROOT/docker-compose.images.yml"; then
        return 1
      fi
      printf '%s\n' traefik postgres redis api worker email_worker maintenance_worker beat web minio minio-init
      ;;
    "ps --format {{.Image}} api")
      # The image the running api container was started from.
      [[ -z ${STUB_RUNNING_IMAGE:-} ]] || printf '%s\n' "$STUB_RUNNING_IMAGE"
      ;;
    "ps "*)
      [[ -z ${STUB_DOCKER_DOWN:-} ]] || return 1
      printf "%s|%s|%s\n" traefik running "Up 2 hours" postgres running "Up 2 hours (healthy)" \
        api running "Up 2 hours (healthy)" worker exited "Exited (1) 5 minutes ago"
      ;;
    "exec -T postgres pg_isready "*) [[ -z ${STUB_PG_DOWN:-} ]] ;;
    # What restore asks the database (rollback runs restore's steps).
    "exec -T postgres psql -U "*" -d postgres "*)
      cat >/dev/null
      [[ -z ${STUB_MAINT_FAIL:-} ]] || return 1
      echo 1
      ;;
    "exec -T postgres psql "*) echo 42 ;;
    "exec -T postgres dropdb "*) [[ -z ${STUB_DROP_FAIL:-} ]] ;;
    "exec -T postgres createdb "*) return 0 ;;
    "exec -T postgres pg_restore "*)
      cat >"$STUB_DIR/restored"
      [[ -z ${STUB_RESTORE_FAIL:-} ]]
      ;;
    "stop"*)
      # The images file in place when FreeFrame was first stopped.
      [[ -e $STUB_DIR/images-at-stop ]] || cp "$FF_ROOT/docker-compose.images.yml" "$STUB_DIR/images-at-stop" 2>/dev/null || true
      [[ -z ${STUB_STOP_FAIL:-} ]]
      ;;
    "exec -T postgres pg_dump "*)
      [[ -z ${STUB_PG_DOWN:-} ]] || return 1
      printf 'PGDMP current database %s\n' "$(_ff_version_in "$(cat "$FF_ROOT/docker-compose.images.yml")" 2>/dev/null)"
      ;;
    *"localhost:8000/health"*)
      [[ -z ${STUB_HEALTH_HANG:-} ]] || _stub_hang health
      [[ -z ${STUB_API_DOWN:-} ]]
      ;;
    pull*)
      [[ -z ${STUB_PULL_HANG:-} ]] || _stub_hang pull
      [[ -z ${STUB_PULL_FAIL:-} ]]
      ;;
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

# STUB_MV_FAIL=NAME: moving a downloaded file onto $FF_ROOT/NAME fails (a full or read-only
# disk at just the wrong moment). Every other mv is real.
# STUB_MV_OLD_FAIL=NAME: putting NAME back from a download's stage/old fails.
# STUB_MV_HANG=NAME: moving onto $FF_ROOT/NAME hangs first (a signal in the
# middle of the replace).
if [[ -n ${STUB_MV_FAIL:-}${STUB_MV_OLD_FAIL:-}${STUB_MV_HANG:-} ]]; then
  mv() {
    if [[ -n ${STUB_MV_FAIL:-} && ${!#} == "$FF_ROOT/$STUB_MV_FAIL" && $* == *"/.download."* ]]; then return 1; fi
    if [[ -n ${STUB_MV_OLD_FAIL:-} && ${!#} == "$FF_ROOT/$STUB_MV_OLD_FAIL" && $* == */old/* ]]; then return 1; fi
    if [[ -n ${STUB_MV_HANG:-} && ${!#} == "$FF_ROOT/$STUB_MV_HANG" ]]; then _stub_hang mv; fi
    command mv "$@"
  }
fi
