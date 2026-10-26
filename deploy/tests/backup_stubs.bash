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

# The database's state, kept in a file so it carries across runs like a
# real database: data (the default, or STUB_DB_STATE), absent, empty.
_stub_db() { cat "$STUB_DIR/dbstate" 2>/dev/null || printf '%s' "${STUB_DB_STATE:-data}"; }
_stub_db_set() { printf '%s' "$1" >"$STUB_DIR/dbstate"; }

# _stub_hang NAME: for the signal tests. Note this process group (the test
# signals all of it, as systemctl stop and Ctrl-C do), say the long step has
# started, and wait.
_stub_hang() {
  local -a st
  read -r -a st </proc/$BASHPID/stat
  printf '%s' "${st[4]}" >"$STUB_DIR/pgid"
  : >"$STUB_DIR/hanging-$1"
  sleep 30
}

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
      if [[ -n ${STUB_DUMP_FAIL:-} || $(_stub_db) == absent ]]; then
        printf 'PGDMP-half-written'
        return 1
      fi
      if [[ -n ${STUB_DUMP_HANG:-} ]]; then
        printf 'PGDMP-so-far'
        _stub_hang pg_dump
      fi
      if [[ -n ${STUB_DUMP_GARBAGE:-} ]]; then
        printf 'pg_dump: error: connection failed\n'
        return 0
      fi
      if [[ $(_stub_db) == empty ]]; then
        printf 'PGDMP empty database\n'
      else
        printf 'PGDMP current database %s\n' "${STUB_DB_CONTENT:-v2}"
      fi
      ;;
    "exec -T postgres pg_isready "*) [[ -z ${STUB_PG_DOWN:-} ]] ;;
    # The maintenance database: does FreeFrame's database exist?
    "exec -T postgres psql -U "*" -d postgres "*)
      cat >"$STUB_DIR/maint-sql"
      [[ -z ${STUB_MAINT_FAIL:-} ]] || return 1
      if [[ $(_stub_db) == absent ]]; then echo 0; else echo 1; fi
      ;;
    # FreeFrame's database: how many tables?
    "exec -T postgres psql "*)
      [[ -z ${STUB_DB_OPEN_FAIL:-} && $(_stub_db) != absent ]] || return 1
      if [[ $(_stub_db) == empty ]]; then echo 0; else echo 42; fi
      ;;
    "exec -T postgres dropdb "*)
      [[ -z ${STUB_DROP_FAIL:-} ]] || return 1
      _stub_db_set absent
      ;;
    "exec -T postgres createdb "*)
      [[ -z ${STUB_CREATE_FAIL:-} ]] || return 1
      _stub_db_set empty
      ;;
    "exec -T postgres pg_restore "*)
      cat >"$STUB_DIR/restored"
      [[ -z ${STUB_RESTORE_HANG:-} ]] || _stub_hang pg_restore
      [[ -z ${STUB_RESTORE_FAIL:-} ]] || return 1
      _stub_db_set data
      ;;
    *"localhost:8000/health"*) [[ -z ${STUB_API_DOWN:-} ]] ;;
    "up -d --no-build postgres") [[ -z ${STUB_PG_DOWN:-} ]] ;;
    "up -d"*) [[ -z ${STUB_UP_FAIL:-} ]] ;;
    "stop "*) [[ -z ${STUB_STOP_FAIL:-} ]] ;;
    *) return 0 ;;
  esac
}
