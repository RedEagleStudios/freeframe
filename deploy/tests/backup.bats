#!/usr/bin/env bats
# `freeframe backup`, `freeframe restore` and the nightly backup timer, with
# every side effect stubbed (backup_stubs.bash). Like install.bats, each flow
# runs in a fresh bash under the script's own strict mode.

load helpers

setup() {
  common_setup
  export STUB_DIR="$BATS_TEST_TMPDIR"
  export CALLS="$BATS_TEST_TMPDIR/calls"
  : >"$CALLS"
  STUBS="${BATS_TEST_DIRNAME}/backup_stubs.bash"
  ENV="$FF_ROOT/.env.prod"
  BK="$FF_ROOT/backups"
  # An installed server: a settings file with a quoted database user, so the
  # names must come through env_get.
  printf "POSTGRES_USER='ffuser'\nPOSTGRES_DB=ffdb\nPOSTGRES_PASSWORD=pw\n" >"$ENV"
  chmod 600 "$ENV"
}

# ff ARGS...: run `freeframe ARGS` with the stubs.
ff() {
  run bash -c 'set -Eeuo pipefail; source "$1"; source "$2"; shift 2; main "$@"' \
    ff-run "$FF_SCRIPT" "$STUBS" "$@"
}

assert_order() {
  local last=0 pat n
  for pat in "$@"; do
    n=$(grep -n -F -- "$pat" "$CALLS" | awk -F: -v l="$last" '$1 > l { print $1; exit }')
    if [[ -z $n ]]; then
      echo "not found after line $last: $pat"
      echo "--- calls ---"
      cat "$CALLS"
      return 1
    fi
    last=$n
  done
}

assert_not_called() {
  if grep -q -F -- "$1" "$CALLS"; then
    echo "unexpected call: $1"
    cat "$CALLS"
    return 1
  fi
}

assert_out() {
  if [[ $output != *"$1"* ]]; then
    echo "expected output to contain: $1"
    echo "--- output ---"
    echo "$output"
    return 1
  fi
}

# old_backup STAMP [CONTENT]: an earlier backup and its settings copy.
old_backup() {
  mkdir -p "$BK"
  printf 'PGDMP %s\n' "${2:-old $1}" >"$BK/freeframe-$1.dump"
  printf 'X=1\n' >"$BK/freeframe-$1.env.prod"
}

dumps() { (cd "$BK" && LC_ALL=C ls freeframe-*.dump 2>/dev/null) || true; }

# ---------------------------------------------------------------- backup

@test "backup: writes to a .partial name and renames it only when the dump is complete" {
  ff backup
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -qx 'dc exec -T postgres pg_dump -Fc -U ffuser ffdb' "$CALLS"
  # While pg_dump ran, only the .partial name existed.
  grep -q '^freeframe-[0-9]\{8\}-[0-9]\{6\}\.dump\.partial$' "$STUB_DIR/during-dump"
  if grep -q '\.dump$' "$STUB_DIR/during-dump"; then false; fi
  local d
  d=$(dumps)
  [[ $d =~ ^freeframe-[0-9]{8}-[0-9]{6}\.dump$ ]] || { echo "dumps: $d"; false; }
  assert_eq "$(cat "$BK/$d")" "PGDMP current database v2"
  cmp "$ENV" "$BK/${d%.dump}.env.prod"
  [ -z "$(ls "$BK" | grep partial)" ]
  assert_out "$BK/$d"
}

@test "backup: the dump and the settings copy are private (600, folder 700)" {
  ff backup
  [ "$status" -eq 0 ]
  local d
  d=$(dumps)
  assert_eq "$(stat -c %a "$BK/$d")" 600
  assert_eq "$(stat -c %a "$BK/${d%.dump}.env.prod")" 600
  assert_eq "$(stat -c %a "$BK")" 700
}

@test "backup: a failed dump leaves no .dump and removes the .partial" {
  old_backup 20261020-030000
  export STUB_DUMP_FAIL=1
  ff backup
  [ "$status" -eq 1 ]
  # The .partial existed while pg_dump ran, and is gone now.
  grep -q '\.dump\.partial$' "$STUB_DIR/during-dump"
  assert_eq "$(ls -A "$BK" | grep -v '^\.' | tr '\n' ' ')" \
    "freeframe-20261020-030000.dump freeframe-20261020-030000.env.prod "
  assert_out "could not be backed up"
  assert_out "No new backup was made"
  assert_out "install.log"
}

@test "backup: output that is not a database dump is not kept" {
  export STUB_DUMP_GARBAGE=1
  ff backup
  [ "$status" -eq 1 ]
  [ -z "$(dumps)" ]
  [ -z "$(ls -A "$BK" | grep -v '^\.')" ]
  assert_out "No new backup was made"
}

@test "backup: keeps the newest 7 with their settings copies" {
  local s
  for s in 01 02 03 04 05 06 07 08; do old_backup "202610$s-030000"; done
  ff backup
  [ "$status" -eq 0 ]
  assert_eq "$(dumps | wc -l | tr -d ' ')" 7
  [ ! -e "$BK/freeframe-20261001-030000.dump" ]
  [ ! -e "$BK/freeframe-20261002-030000.env.prod" ]
  [ -e "$BK/freeframe-20261003-030000.dump" ]
}

@test "backup: refuses when free space is below twice the last dump" {
  old_backup 20261020-030000
  export STUB_BIG_LAST=$((700 * 1048576)) STUB_BIG_LAST_FILE="$BK/freeframe-20261020-030000.dump"
  export STUB_FREE_MB=1300
  ff backup
  [ "$status" -eq 1 ]
  assert_out "1300 MB free"
  assert_out "at least 1400 MB"
  assert_not_called pg_dump
  assert_eq "$(dumps)" "freeframe-20261020-030000.dump"
  # With room for twice the last one, it goes ahead.
  export STUB_FREE_MB=1500
  ff backup
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "backup: refuses below 1 GB free even when the last dump was small" {
  export STUB_FREE_MB=1000
  ff backup
  [ "$status" -eq 1 ]
  assert_out "1000 MB free"
  assert_out "at least 1024 MB"
  assert_not_called pg_dump
  [ -z "$(dumps)" ]
}

@test "backup: waits up to a minute for the database (the catch-up run at boot)" {
  export STUB_PG_READY_AFTER=4
  ff backup --quiet
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(grep -c '^sleep 5$' "$CALLS")" 3
  assert_order "pg_isready" "pg_isready" "pg_isready" "pg_isready" "pg_dump"
}

@test "backup: a database that never answers is refused after the wait, with no dump" {
  export STUB_PG_DOWN=1
  ff backup --quiet
  [ "$status" -eq 1 ]
  assert_out "FreeFrame's database is not running, so no backup was made."
  assert_out "sudo freeframe restart"
  assert_not_called pg_dump
  # 60 seconds in 5-second steps.
  assert_eq "$(grep -c '^sleep 5$' "$CALLS")" 12
}

@test "backup --quiet prints nothing on success" {
  ff backup --quiet
  [ "$status" -eq 0 ]
  assert_eq "$output" ""
  [ -n "$(dumps)" ]
}

@test "backup --quiet prints a plain error on failure" {
  export STUB_DUMP_FAIL=1
  ff backup --quiet
  [ "$status" -eq 1 ]
  assert_out "could not be backed up"
  export STUB_DUMP_FAIL="" STUB_FREE_MB=10
  ff backup --quiet
  [ "$status" -eq 1 ]
  assert_out "not enough free disk space"
}

@test "backup: says plainly that the backups stay on this server" {
  ff backup
  [ "$status" -eq 0 ]
  assert_out "on this server only"
  assert_out "media"
}

@test "backup: without an installed FreeFrame it says so" {
  rm -f "$ENV"
  ff backup
  [ "$status" -eq 1 ]
  assert_out "not installed"
  assert_not_called "dc "
}

@test "backup: not root exits 2 and names sudo" {
  export STUB_NOT_ROOT=1
  ff backup
  [ "$status" -eq 2 ]
  assert_out "sudo freeframe backup"
  assert_not_called "dc "
}

@test "backup: a second backup or restore at the same time is refused" {
  command -v flock >/dev/null || skip "no flock"
  exec 8>>"$FF_ROOT/.freeframe.lock"
  flock -n 8
  ff backup
  exec 8>&-
  [ "$status" -eq 1 ]
  assert_out "Another backup or restore is running"
  assert_not_called pg_dump
}

@test "backup: with no log file possible, a failure does not point at one" {
  mkdir "$FF_ROOT/install.log"
  export STUB_DUMP_FAIL=1
  ff backup --quiet
  [ "$status" -eq 1 ]
  assert_out "could not be backed up"
  if [[ $output == *"/dev/null"* || $output == *"end of"* ]]; then echo "$output"; false; fi
}

@test "backup: a failing step in it is checked, not left to an ERR trap that never fires" {
  # backup_create runs inside `if !`, so no ERR trap can catch a failure in
  # it. A date that cannot be read must still stop it, with no odd file name.
  export STUB_STAMP_FAIL=1
  ff backup
  [ "$status" -eq 1 ]
  assert_out "could not be named"
  assert_not_called pg_dump
  [ -z "$(ls -A "$BK" | grep -v '^\.')" ]
  # And there is no dead ERR handler left in the script.
  if grep -q '_backup_on_error' "$FF_SCRIPT"; then false; fi
}

@test "backup: refusals and failures are written to install.log too" {
  export STUB_FREE_MB=500
  ff backup
  [ "$status" -eq 1 ]
  grep -q "not enough free disk space" "$FF_ROOT/install.log"
  grep -q "No new backup was made" "$FF_ROOT/install.log"
  export STUB_FREE_MB="" STUB_DUMP_FAIL=1
  ff backup --quiet
  [ "$status" -eq 1 ]
  grep -q "could not be backed up" "$FF_ROOT/install.log"
  # Settings values never reach the log.
  if grep -q "pw" "$FF_ROOT/install.log"; then cat "$FF_ROOT/install.log"; false; fi
}

@test "backup: a lock refusal is written to install.log" {
  command -v flock >/dev/null || skip "no flock"
  exec 8>>"$FF_ROOT/.freeframe.lock"
  flock -n 8
  ff backup --quiet
  exec 8>&-
  [ "$status" -eq 1 ]
  grep -q "Another backup or restore is running" "$FF_ROOT/install.log"
}

@test "restore: a failed safety copy is in install.log once, not twice" {
  old_backup 20261020-030000
  export STUB_DUMP_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_eq "$(grep -c "No new backup was made" "$FF_ROOT/install.log")" 1
}

@test "backup: the log records the run" {
  ff backup
  [ "$status" -eq 0 ]
  grep -q 'freeframe backup' "$FF_ROOT/install.log"
  assert_eq "$(stat -c %a "$FF_ROOT/install.log")" 600
}

@test "parse_args: --quiet and -q set FF_QUIET" {
  parse_args backup --quiet
  assert_eq "$FF_QUIET" 1
  parse_args -q backup
  assert_eq "$FF_QUIET" 1
  parse_args backup
  assert_eq "$FF_QUIET" ""
}

# ---------------------------------------------------------------- restore: list

@test "restore without FILE lists backups newest first with sizes and dates" {
  old_backup 20261024-030512
  old_backup 20261026-030001 "$(head -c 3000 /dev/zero | tr '\0' x)"
  old_backup 20261025-031500
  ff restore
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  local a b c
  a=$(grep -n 'freeframe-20261026-030001.dump' <<<"$output" | head -1 | cut -d: -f1)
  b=$(grep -n 'freeframe-20261025-031500.dump' <<<"$output" | head -1 | cut -d: -f1)
  c=$(grep -n 'freeframe-20261024-030512.dump' <<<"$output" | head -1 | cut -d: -f1)
  [ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ]
  ((a < b && b < c)) || { echo "$output"; false; }
  [[ $(sed -n "${a}p" <<<"$output") == *"2026-10-26 03:00:01"*"KB"* ]] || { echo "$output"; false; }
  [[ $(sed -n "${c}p" <<<"$output") == *"2026-10-24 03:05:12"*"bytes"* ]] || { echo "$output"; false; }
  assert_out "sudo freeframe restore '$BK/freeframe-20261026-030001.dump'"
  assert_not_called "dc "
}

@test "restore without FILE and no backups says how to make one" {
  ff restore
  [ "$status" -eq 0 ]
  assert_out "no backups"
  assert_out "sudo freeframe backup"
}

# ---------------------------------------------------------------- restore: refusals

@test "restore without --yes and no terminal refuses and changes nothing" {
  old_backup 20261020-030000
  ff restore "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 2 ]
  assert_out "no terminal"
  assert_out "--yes"
  assert_not_called "dc "
}

@test "restore: answering no cancels and changes nothing" {
  old_backup 20261020-030000
  printf 'n\n' >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff restore "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_out "replaces"
  assert_out "Restore cancelled. Nothing was changed."
  assert_not_called "dc "
}

@test "restore: answering yes goes ahead" {
  old_backup 20261020-030000
  printf 'y\n' >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff restore "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "dc exec -T postgres dropdb" "dc exec -T postgres pg_restore"
}

@test "restore: a file that is not a backup is refused before any change" {
  printf 'CREATE TABLE x();\n' >"$BATS_TEST_TMPDIR/backup.sql"
  ff restore --yes "$BATS_TEST_TMPDIR/backup.sql"
  [ "$status" -eq 1 ]
  assert_out "is not a FreeFrame database backup"
  assert_not_called "dc "
}

@test "restore: a missing file is refused; a bare name is found in the backups folder" {
  ff restore --yes nosuch.dump
  [ "$status" -eq 1 ]
  assert_out "There is no backup file"
  assert_not_called "dc "
  old_backup 20261020-030000
  ff restore --yes freeframe-20261020-030000.dump
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(cat "$STUB_DIR/restored")" "PGDMP old 20261020-030000"
}

@test "restore: not root exits 2" {
  export STUB_NOT_ROOT=1
  ff restore
  [ "$status" -eq 2 ]
  assert_out "sudo freeframe restore"
}

@test "restore: every printed command quotes its path, so it can be pasted" {
  # A backup copied in under a folder whose name has spaces and a quote.
  local d="$BATS_TEST_TMPDIR/old server's backups"
  mkdir -p "$d"
  printf 'PGDMP old\n' >"$d/freeframe-20261020-030000.dump"
  export STUB_RESTORE_FAIL=1
  ff restore --yes "$d/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  local q="'$BATS_TEST_TMPDIR/old server'\\''s backups/freeframe-20261020-030000.dump'"
  assert_out "To try again, run: sudo freeframe restore --yes $q"
  # The printed command, run through a shell, gives back the exact path.
  local cmd
  cmd=${output#*To try again, run: sudo freeframe restore --yes }
  cmd="${cmd%%.dump\'*}.dump'"
  eval "set -- $cmd"
  assert_eq "$#" 1
  assert_eq "$1" "$d/freeframe-20261020-030000.dump"
  # The undo command is quoted too, and so is the no-terminal hint.
  local safety
  safety=$(dumps | tail -1)
  assert_out "run: sudo freeframe restore --yes '$BK/$safety'"
  unset STUB_RESTORE_FAIL
  rm -f "$FF_ROOT/.restore-undo"
  export FF_YES=""
  ff restore "$d/freeframe-20261020-030000.dump"
  [ "$status" -eq 2 ]
  assert_out "add --yes: sudo freeframe restore --yes $q"
}

@test "restore: two files is a usage error" {
  ff restore a.dump b.dump
  [ "$status" -eq 2 ]
  assert_not_called "dc "
}

# ---------------------------------------------------------------- restore: the steps

@test "restore calls stop, dropdb, createdb, pg_restore, up in that order" {
  old_backup 20261020-030000
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "dc up -d --no-build postgres" "dc exec -T postgres pg_isready" \
    "dc stop " \
    "dc exec -T postgres dropdb -U ffuser --if-exists --force ffdb" \
    "dc exec -T postgres createdb -U ffuser -O ffuser ffdb" \
    "dc exec -T postgres pg_restore -U ffuser -d ffdb --no-owner --no-acl --single-transaction" \
    "dc up -d --no-build" "localhost:8000/health"
  # Every service except postgres and traefik is stopped.
  local stop
  stop=$(grep '^dc stop ' "$CALLS")
  assert_eq "$stop" "dc stop redis api worker email_worker maintenance_worker beat web minio minio-init"
  # The dump's bytes reached pg_restore.
  assert_eq "$(cat "$STUB_DIR/restored")" "PGDMP old 20261020-030000"
  assert_out "The database is back as it was"
}

@test "restore: a safety copy of the current database is saved first, as a normal backup" {
  local s
  for s in 14 15 16 17 18 19 20; do old_backup "202610$s-030000"; done
  # Restore the oldest of 7: the safety copy must not prune it away.
  ff restore --yes "$BK/freeframe-20261014-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "dc stop " "dc exec -T postgres pg_dump -Fc -U ffuser ffdb" "dc exec -T postgres dropdb"
  assert_eq "$(dumps | wc -l | tr -d ' ')" 8
  [ -e "$BK/freeframe-20261014-030000.dump" ]
  local safety
  safety=$(dumps | tail -1)
  [[ $safety != freeframe-20261020-030000.dump ]]
  assert_eq "$(cat "$BK/$safety")" "PGDMP current database v2"
  [ -e "$BK/${safety%.dump}.env.prod" ]
  assert_out "sudo freeframe restore --yes '$BK/$safety'"
  assert_eq "$(cat "$STUB_DIR/restored")" "PGDMP old 20261014-030000"
}

@test "restore: a database that is not there has nothing to save, and the restore goes ahead" {
  old_backup 20261020-030000
  export STUB_DB_STATE=absent
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_not_called pg_dump
  assert_out "nothing to save"
  assert_order "dc exec -T postgres dropdb" "dc exec -T postgres pg_restore"
  # The check goes through the maintenance database, with the name as a
  # psql variable.
  grep -q -- "-d postgres -v db=ffdb" "$CALLS"
  grep -q "datname = :'db'" "$STUB_DIR/maint-sql"
}

@test "restore: an empty database (no tables) has nothing to save" {
  old_backup 20261020-030000
  export STUB_DB_STATE=empty
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_not_called pg_dump
  assert_out "nothing to save"
  [ ! -e "$FF_ROOT/.restore-undo" ]
}

@test "restore: a database that exists but cannot be opened is never dropped" {
  old_backup 20261020-030000
  export STUB_DB_OPEN_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called pg_dump
  assert_not_called dropdb
  assert_out "could not be checked"
  assert_out "before changing anything"
  assert_order "dc stop " "dc up -d --no-build"
}

@test "restore: the maintenance check failing is never read as nothing to save" {
  old_backup 20261020-030000
  export STUB_MAINT_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called dropdb
  assert_out "could not be checked"
}

@test "restore: a retry after a failed restore keeps the first safety copy as the undo" {
  old_backup 20261020-030000
  export STUB_RESTORE_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  local safety1
  safety1=$(dumps | tail -1)
  assert_eq "$(cat "$BK/$safety1")" "PGDMP current database v2"
  assert_eq "$(cat "$FF_ROOT/.restore-undo")" "$BK/$safety1"
  assert_eq "$(stat -c %a "$FF_ROOT/.restore-undo")" 600
  # The retry fails too: no copy of the empty database is taken, and the
  # undo still points at the first copy.
  : >"$CALLS"
  sleep 1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called pg_dump
  assert_out "nothing new to save"
  assert_out "sudo freeframe restore --yes '$BK/$safety1'"
  assert_eq "$(dumps | tail -1)" "$safety1"
  assert_eq "$(cat "$FF_ROOT/.restore-undo")" "$BK/$safety1"
  # The undo itself then works, puts the first copy back and clears the record.
  unset STUB_RESTORE_FAIL
  ff restore --yes "$BK/$safety1"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(cat "$STUB_DIR/restored")" "PGDMP current database v2"
  [ ! -e "$FF_ROOT/.restore-undo" ]
  # Having just put that copy back, it is not offered as a way back again.
  if [[ $output == *"to go back to it"* ]]; then echo "$output"; false; fi
}

@test "restore: a retry over a database with data copies it too, but the undo stays the first copy" {
  old_backup 20261020-030000
  export STUB_API_DOWN=1 FF_API_WAIT=10
  # Attempt 1 puts the backup back but FreeFrame does not come up: not done.
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  local safety1
  safety1=$(dumps | tail -1)
  [ -e "$FF_ROOT/.restore-undo" ]
  unset STUB_API_DOWN
  sleep 1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -q 'pg_dump' "$CALLS"
  assert_out "is kept too: $BK/$safety1"
  assert_out "to go back to it, run: sudo freeframe restore --yes '$BK/$safety1'"
  [ ! -e "$FF_ROOT/.restore-undo" ]
}

@test "restore: a recorded copy that was deleted is said, and the record dropped" {
  old_backup 20261020-030000
  printf '%s\n' "$BK/freeframe-20261001-000000.dump" >"$FF_ROOT/.restore-undo"
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "no longer there"
  grep -q pg_dump "$CALLS"
}

@test "backup: the recorded undo copy is never pruned and not used for the size rule" {
  local s
  old_backup 20261001-030000 "the copy to undo with"
  printf '%s\n' "$BK/freeframe-20261001-030000.dump" >"$FF_ROOT/.restore-undo"
  for s in 02 03 04 05 06 07 08 09; do old_backup "202610$s-030000"; done
  # The recorded copy is huge, the others are small: it must not count.
  export STUB_BIG_LAST=$((900 * 1048576)) STUB_BIG_LAST_FILE="$BK/freeframe-20261001-030000.dump"
  export STUB_FREE_MB=1100
  ff backup
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ -e "$BK/freeframe-20261001-030000.dump" ]
  [ -e "$BK/freeframe-20261001-030000.env.prod" ]
  # 7 normal ones are kept besides it.
  assert_eq "$(dumps | wc -l | tr -d ' ')" 8
  [ ! -e "$BK/freeframe-20261003-030000.dump" ]
  [ -e "$BK/freeframe-20261004-030000.dump" ]
}

@test "backup_prune leaves a protected dump alone and does not count it" {
  local s
  for s in 01 02 03 04; do old_backup "202610$s-030000"; done
  backup_prune "$BK" 2 "$BK/freeframe-20261001-030000.dump"
  assert_eq "$(dumps | tr '\n' ' ')" "freeframe-20261001-030000.dump freeframe-20261003-030000.dump freeframe-20261004-030000.dump "
}

@test "restore without FILE mentions an unfinished restore's undo copy" {
  old_backup 20261020-030000
  printf '%s\n' "$BK/freeframe-20261020-030000.dump" >"$FF_ROOT/.restore-undo"
  ff restore
  [ "$status" -eq 0 ]
  assert_out "A restore did not finish"
  assert_out "sudo freeframe restore --yes '$BK/freeframe-20261020-030000.dump'"
}

@test "restore: a failed safety copy changes nothing and starts FreeFrame again" {
  old_backup 20261020-030000
  export STUB_DUMP_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called dropdb
  assert_not_called pg_restore
  assert_order "dc stop " "pg_dump" "dc up -d --no-build"
  assert_out "before changing anything"
  [ -z "$(ls -A "$BK" | grep partial)" ]
}

@test "restore: too little disk for the safety copy changes nothing" {
  old_backup 20261020-030000
  export STUB_FREE_MB=10
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called dropdb
  assert_out "not enough free disk space"
  assert_out "before changing anything"
}

@test "restore: the database server not starting stops before any change" {
  old_backup 20261020-030000
  export STUB_PG_DOWN=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called "dc stop"
  assert_not_called dropdb
  assert_out "nothing was changed"
}

@test "restore: dropdb failing leaves the database as it was and starts FreeFrame again" {
  old_backup 20261020-030000
  export STUB_DROP_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called createdb
  assert_order "dropdb" "dc up -d --no-build"
  assert_out "unchanged"
  assert_out "install.log"
}

@test "restore: it says FreeFrame was started again only when it was" {
  old_backup 20261020-030000
  export STUB_DROP_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_out "FreeFrame has been started again."
  export STUB_UP_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  if [[ $output == *"has been started again"* ]]; then echo "$output"; false; fi
  assert_out "FreeFrame could not be started again. To start it, run: sudo freeframe restart"
  # The same for a stop that fails, and a database that cannot be checked.
  export STUB_DROP_FAIL="" STUB_STOP_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_out "FreeFrame could not be stopped, so nothing was changed."
  assert_out "FreeFrame could not be started again."
  export STUB_STOP_FAIL="" STUB_DB_OPEN_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_out "FreeFrame could not be started again."
}

@test "restore: createdb failing after the drop says the database is gone and how to retry" {
  old_backup 20261020-030000
  export STUB_CREATE_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_not_called pg_restore
  # FreeFrame stays stopped: an app on an empty database would offer its
  # setup page to anyone.
  [ "$(grep -c '^dc up -d' "$CALLS")" -eq 1 ]
  assert_out "The database has been removed"
  assert_out "sudo freeframe restore --yes '$BK/freeframe-20261020-030000.dump'"
  assert_out "install.log"
}

@test "restore: pg_restore failing says the database is empty, FreeFrame stays stopped, and how to retry" {
  old_backup 20261020-030000
  export STUB_RESTORE_FAIL=1
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  [ "$(grep -c '^dc up -d' "$CALLS")" -eq 1 ]
  assert_out "The database is empty now"
  assert_out "FreeFrame is stopped"
  assert_out "sudo freeframe restore --yes '$BK/freeframe-20261020-030000.dump'"
  local safety
  safety=$(dumps | tail -1)
  assert_out "sudo freeframe restore --yes '$BK/$safety'"
  assert_out "install.log"
  assert_eq "$(cat "$FF_ROOT/.restore-undo")" "$BK/$safety"
}

@test "restore: FreeFrame not answering afterwards says the backup is back but the app is not" {
  old_backup 20261020-030000
  export STUB_API_DOWN=1 FF_API_WAIT=10
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_out "The backup has been put back"
  assert_out "sudo freeframe logs"
}

@test "restore: the log records what happened" {
  old_backup 20261020-030000
  ff restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 0 ]
  grep -q 'freeframe restore' "$FF_ROOT/install.log"
  grep -q 'The database is back as it was' "$FF_ROOT/install.log"
}

# ---------------------------------------------------------------- signals

# ff_signal SIG STEP ARGS...: run `freeframe ARGS` in its own process group,
# wait until the stubbed STEP hangs, then send SIG to the whole group (as
# systemctl stop, a shutdown or Ctrl-C do). INT cannot be tested this way:
# bash starts background jobs with SIGINT ignored, and an ignored signal
# cannot be trapped. INT, TERM and HUP share one handler.
ff_signal() {
  local sig=$1 step=$2 pid n=0
  shift 2
  command -v setsid >/dev/null || skip "no setsid"
  setsid bash -c 'set -Eeuo pipefail; source "$1"; source "$2"; shift 2; main "$@"' \
    ff-run "$FF_SCRIPT" "$STUBS" "$@" >"$BATS_TEST_TMPDIR/out" 2>&1 &
  pid=$!
  while [[ ! -e $STUB_DIR/hanging-$step ]]; do
    sleep 0.1
    n=$((n + 1))
    if ((n > 150)); then
      echo "the step $step never started"
      cat "$BATS_TEST_TMPDIR/out"
      return 1
    fi
  done
  kill -"$sig" -- -"$(cat "$STUB_DIR/pgid")"
  status=0
  wait "$pid" || status=$?
  output=$(cat "$BATS_TEST_TMPDIR/out")
}

@test "backup: stopped part-way (TERM), it removes the part-written files and says so" {
  export STUB_DUMP_HANG=1
  ff_signal TERM pg_dump backup --quiet
  [ "$status" -eq 1 ]
  assert_out "The backup was stopped (SIGTERM) before it finished."
  assert_out "No new backup was made"
  [ -z "$(ls -A "$BK" | grep -v '^\.')" ]
}

@test "backup: files a killed backup left behind are removed by the next one" {
  mkdir -p "$BK"
  printf 'PGDMP-half' >"$BK/freeframe-20261020-030000.dump.partial"
  printf 'X=1' >"$BK/freeframe-20261020-030000.env.prod.partial"
  old_backup 20261019-030000
  ff backup
  [ "$status" -eq 0 ]
  [ -z "$(ls -A "$BK" | grep partial)" ]
  [ -e "$BK/freeframe-20261019-030000.dump" ]
  grep -q 'left by a backup that did not finish' "$FF_ROOT/install.log"
}

@test "restore: stopped (TERM) while the backup goes in, it says the database is empty and how to go on" {
  old_backup 20261020-030000
  export STUB_RESTORE_HANG=1
  ff_signal TERM pg_restore restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ]
  assert_out "The restore was stopped (SIGTERM) while putting the backup back"
  assert_out "The database is empty now"
  assert_out "sudo freeframe restore --yes '$BK/freeframe-20261020-030000.dump'"
  local safety
  safety=$(dumps | tail -1)
  assert_out "sudo freeframe restore --yes '$BK/$safety'"
  assert_out "install.log"
  # FreeFrame was not started on the empty database, and the undo is kept.
  [ "$(grep -c '^dc up -d' "$CALLS")" -eq 1 ]
  assert_eq "$(cat "$FF_ROOT/.restore-undo")" "$BK/$safety"
  grep -q "stopped (SIGTERM)" "$FF_ROOT/install.log"
}

@test "restore: stopped (HUP) while saving the safety copy, nothing was changed and no part file is left" {
  old_backup 20261020-030000
  export STUB_DUMP_HANG=1
  ff_signal HUP pg_dump restore --yes "$BK/freeframe-20261020-030000.dump"
  [ "$status" -eq 1 ] || { echo "status $status"; echo "$output"; cat "$CALLS"; false; }
  assert_out "The restore was stopped (SIGHUP) while saving a copy of the current database"
  assert_out "the database has not been changed"
  assert_not_called dropdb
  [ -z "$(ls -A "$BK" | grep partial)" ]
}

# ---------------------------------------------------------------- the timer

UNITS="${BATS_TEST_DIRNAME}/../systemd"

@test "the timer runs every night at 03:00, catches up, with a random 15-minute delay" {
  local t="$UNITS/freeframe-backup.timer"
  assert_has "$t" "OnCalendar=*-*-* 03:00"
  assert_has "$t" "Persistent=true"
  assert_has "$t" "RandomizedDelaySec=15m"
  assert_has "$t" "WantedBy=timers.target"
}

@test "the backup service runs freeframe backup --quiet once" {
  local s="$UNITS/freeframe-backup.service"
  assert_has "$s" "Type=oneshot"
  assert_has "$s" "ExecStart=/usr/local/bin/freeframe backup --quiet"
  assert_has "$s" "After=docker.service"
}

@test "the units pass systemd-analyze verify" {
  command -v systemd-analyze >/dev/null || skip "no systemd-analyze"
  local d="$BATS_TEST_TMPDIR/units"
  mkdir -p "$d"
  cp "$UNITS"/freeframe-backup.* "$d/"
  # verify checks that ExecStart exists; point it at a stand-in.
  printf '#!/bin/sh\n' >"$d/freeframe"
  chmod 755 "$d/freeframe"
  sed -i "s#/usr/local/bin/freeframe#$d/freeframe#" "$d/freeframe-backup.service"
  run systemd-analyze verify "$d/freeframe-backup.service" "$d/freeframe-backup.timer"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "install_backup_timer installs both units and starts the timer" {
  mkdir -p "$FF_ROOT/systemd" "$BATS_TEST_TMPDIR/etc"
  cp "$UNITS"/freeframe-backup.* "$FF_ROOT/systemd/"
  export FF_SYSTEMD_DIR="$BATS_TEST_TMPDIR/etc"
  systemctl() { printf 'systemctl %s\n' "$*" >>"$CALLS"; }
  install_backup_timer
  cmp "$UNITS/freeframe-backup.service" "$FF_SYSTEMD_DIR/freeframe-backup.service"
  cmp "$UNITS/freeframe-backup.timer" "$FF_SYSTEMD_DIR/freeframe-backup.timer"
  assert_eq "$(stat -c %a "$FF_SYSTEMD_DIR/freeframe-backup.timer")" 644
  assert_order "systemctl daemon-reload" "systemctl enable --now freeframe-backup.timer"
}

@test "install_backup_timer fails when the units are missing or systemctl fails" {
  mkdir -p "$BATS_TEST_TMPDIR/etc"
  export FF_SYSTEMD_DIR="$BATS_TEST_TMPDIR/etc"
  systemctl() { printf 'systemctl %s\n' "$*" >>"$CALLS"; }
  run install_backup_timer
  [ "$status" -ne 0 ]
  mkdir -p "$FF_ROOT/systemd"
  cp "$UNITS"/freeframe-backup.* "$FF_ROOT/systemd/"
  systemctl() { return 1; }
  run install_backup_timer
  [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------- helpers

@test "_ff_human_size and _ff_stamp_date" {
  assert_eq "$(_ff_human_size 512)" "512 bytes"
  assert_eq "$(_ff_human_size 3000)" "2 KB"
  assert_eq "$(_ff_human_size $((12 * 1048576 + 400000)))" "12.4 MB"
  assert_eq "$(_ff_human_size $((3 * 1073741824)))" "3.0 GB"
  assert_eq "$(_ff_stamp_date freeframe-20261026-030412.dump)" "2026-10-26 03:04:12"
  assert_eq "$(_ff_stamp_date something.dump)" ""
}
