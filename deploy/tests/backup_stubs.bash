# Stubs for every side effect of `freeframe backup` and `freeframe restore`.
# backup.bats sources this file after the script, in a fresh bash, so these
# definitions replace the real wrappers. Each call is appended to $CALLS, one
# line per call, so a test can check what ran and in which order. The STUB_*
# environment variables are the knobs the tests turn.
#
# Nothing here reaches Docker, PostgreSQL or systemd.

_stub_log() { printf '%s\n' "$*" >>"$CALLS"; }

ff_is_root() { [[ -z ${STUB_NOT_ROOT:-} ]]; }
ff_sleep() { _stub_log "sleep $1"; }
_ff_free_mb() { printf '%s\n' "${STUB_FREE_MB:-80000}"; }

# STUB_BIG_LAST=BYTES: the size the newest existing dump reports, so the
# low-disk rule can be tested without writing a large file.
if [[ -n ${STUB_BIG_LAST:-} ]]; then
  eval "_real_ff_size_bytes() $(declare -f _ff_size_bytes | tail -n +2)"
  _ff_size_bytes() {
    if [[ $1 == "$STUB_BIG_LAST_FILE" ]]; then
      printf '%s\n' "$STUB_BIG_LAST"
    else
      _real_ff_size_bytes "$1"
    fi
  }
fi

# docker compose for this install.
dc() {
  _stub_log "dc $*"
  case "$*" in
    "config --services")
      printf '%s\n' traefik postgres redis api worker email_worker maintenance_worker beat web minio minio-init
      ;;
    "exec -T postgres pg_dump "*)
      # What the backups folder holds while the dump is being written.
      ls -A "$FF_ROOT/backups" >"$STUB_DIR/during-dump"
      if [[ -n ${STUB_DUMP_FAIL:-} ]]; then
        printf 'PGDMP-half-written'
        return 1
      fi
      if [[ -n ${STUB_DUMP_GARBAGE:-} ]]; then
        printf 'pg_dump: error: connection failed\n'
        return 0
      fi
      printf 'PGDMP current database %s\n' "${STUB_DB_CONTENT:-v2}"
      ;;
    "exec -T postgres pg_isready "*) [[ -z ${STUB_PG_DOWN:-} ]] ;;
    "exec -T postgres psql "*) [[ -z ${STUB_DB_MISSING:-} ]] ;;
    "exec -T postgres dropdb "*) [[ -z ${STUB_DROP_FAIL:-} ]] ;;
    "exec -T postgres createdb "*) [[ -z ${STUB_CREATE_FAIL:-} ]] ;;
    "exec -T postgres pg_restore "*)
      cat >"$STUB_DIR/restored"
      [[ -z ${STUB_RESTORE_FAIL:-} ]]
      ;;
    *"localhost:8000/health"*) [[ -z ${STUB_API_DOWN:-} ]] ;;
    "up -d --no-build postgres") [[ -z ${STUB_PG_DOWN:-} ]] ;;
    "up -d"*) [[ -z ${STUB_UP_FAIL:-} ]] ;;
    "stop "*) [[ -z ${STUB_STOP_FAIL:-} ]] ;;
    *) return 0 ;;
  esac
}
