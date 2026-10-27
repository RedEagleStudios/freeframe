#!/usr/bin/env bats
# `freeframe rollback`: back to the version and the database from before an
# update that did not come up. Side effects stubbed (manage_stubs.bash).

load manage_helpers

setup() {
  manage_setup
  installed_server sslip bundled 1.21.0
  remote_release 1.22.0
  local f
  mkdir -p "$BATS_TEST_TMPDIR/before"
  for f in docker-compose.prod.yml docker-compose.images.yml docker-compose.https.yml docker-compose.storage.yml; do
    cp "$FF_ROOT/$f" "$BATS_TEST_TMPDIR/before/$f"
  done
  cp "$FF_ROOT/systemd/freeframe-backup.service" "$BATS_TEST_TMPDIR/before/freeframe-backup.service"
}

# failed_update: an update to 1.22.0 whose new version never answers.
failed_update() {
  STUB_API_DOWN=1 FF_API_WAIT=10 ff update
  [ "$status" -eq 1 ] || { echo "$output"; return 1; }
  [ -e "$FF_ROOT/.update-undo" ]
  UPDATE_BACKUP=$(sed -n 's/^backup=//p' "$FF_ROOT/.update-undo")
  [ -f "$UPDATE_BACKUP" ]
  : >"$CALLS"
}

assert_files_from_before() {
  local f
  for f in docker-compose.prod.yml docker-compose.images.yml docker-compose.https.yml docker-compose.storage.yml; do
    cmp "$BATS_TEST_TMPDIR/before/$f" "$FF_ROOT/$f" || { echo "$f is not as before"; return 1; }
  done
  cmp "$BATS_TEST_TMPDIR/before/freeframe-backup.service" "$FF_ROOT/systemd/freeframe-backup.service"
}

# FreeFrame was started (anything but the database server alone).
assert_not_started() {
  if grep -E '^dc up -d' "$CALLS" | grep -qv '^dc up -d --no-build postgres$'; then
    echo "FreeFrame was started:"
    grep '^dc up' "$CALLS"
    return 1
  fi
}

@test "rollback: stops, puts the setup files back, pins the old version, restores the backup, then starts" {
  failed_update
  ff rollback --yes
  [ "$status" -eq 0 ] || { echo "$output"; cat "$CALLS"; false; }
  # Stopped while the new release's files were still in place.
  grep -q '1.22.0' "$STUB_DIR/images-at-stop"
  assert_order "dc stop " "dc exec -T postgres dropdb" "dc exec -T postgres createdb" \
    "dc exec -T postgres pg_restore" "dc up -d --no-build --remove-orphans" "localhost:8000/health" \
    "probe_health sslip" install_backup_timer
  assert_files_from_before
  assert_has "$ENV" "FREEFRAME_VERSION=1.21.0"
  # The pin was written with env_set after a backup of the settings.
  local b
  b=$(env_backups | tail -1)
  [ -n "$b" ]
  assert_no_key "$FF_ROOT/$b" FREEFRAME_VERSION
  # The database that went back is the backup taken before the update.
  cmp "$UPDATE_BACKUP" "$STUB_DIR/restored"
  [ ! -e "$FF_ROOT/.update-undo" ]
  [ ! -e "$FF_ROOT/.restore-undo" ]
  assert_out "FreeFrame is back on 1.21.0"
  assert_out "delete the FREEFRAME_VERSION line"
}

@test "rollback: a database check that fails never starts the old version, and says a re-run is safe" {
  failed_update
  export STUB_MAINT_FAIL=1
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_not_started
  assert_out "Its setup files and version are back to 1.21.0, but the database is not yet back"
  assert_out "Running sudo freeframe rollback again is safe."
  assert_out "stopped before changing the database"
  assert_no_out "nothing was changed"
  assert_no_out "before changing anything"
  assert_no_out "sudo freeframe restart"
  assert_no_out "FreeFrame has been started again"
  [ -e "$FF_ROOT/.update-undo" ]
}

@test "rollback: a dropdb that fails on a database with data does not start the old version" {
  failed_update
  export STUB_DROP_FAIL=1
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_not_started
  assert_out "rollback again is safe"
  assert_no_out "FreeFrame has been started again"
}

@test "rollback: the database server not starting says the database was not changed, not nothing" {
  failed_update
  export STUB_PG_DOWN=1 FF_DB_WAIT=4
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_out "FreeFrame's database did not start, so the database was not changed."
  assert_out "Its setup files and version are back to 1.21.0"
  assert_not_started
}

@test "rollback: the backup failing to go back leaves FreeFrame stopped, and running it again finishes" {
  failed_update
  export STUB_RESTORE_FAIL=1
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_not_started
  assert_out "To try again, run: sudo freeframe rollback"
  # The other undo command (the copy of what the update left) is not offered.
  assert_no_out "sudo freeframe restore --yes"
  [ -e "$FF_ROOT/.update-undo" ]
  [ -e "$FF_ROOT/.restore-undo" ]
  # While it is unfinished, restart sends you back to the rollback.
  ff restart
  [ "$status" -eq 1 ]
  assert_out "sudo freeframe rollback"
  : >"$CALLS"
  unset STUB_RESTORE_FAIL
  ff rollback --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_files_from_before
  [ ! -e "$FF_ROOT/.update-undo" ]
  [ ! -e "$FF_ROOT/.restore-undo" ]
}

@test "rollback: FreeFrame that cannot be stopped is said, and a re-run is safe" {
  failed_update
  export STUB_STOP_FAIL=1
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_out "FreeFrame could not be stopped."
  assert_out "rollback again is safe"
  assert_not_called "pg_restore"
  [ -e "$FF_ROOT/.update-undo" ]
}

@test "rollback: the old version not answering afterwards is said, with the record kept" {
  failed_update
  export STUB_API_DOWN=1 FF_API_WAIT=10
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_out "FreeFrame 1.21.0 did not answer after the rollback."
  assert_out "rollback again is safe"
  [ -e "$FF_ROOT/.update-undo" ]
}

@test "rollback: with no update record there is nothing to roll back" {
  ff rollback --yes
  [ "$status" -eq 0 ]
  assert_out "There is no update to roll back"
  assert_not_called "dc "
}

@test "rollback: it asks first; no terminal and no --yes refuses" {
  failed_update
  ff rollback
  [ "$status" -eq 2 ]
  assert_out "--yes"
  assert_not_called "dc "
  printf 'n\n' >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff rollback
  [ "$status" -eq 1 ]
  assert_out "This takes FreeFrame back to version 1.21.0"
  assert_out "Rollback cancelled. Nothing was changed."
  assert_not_called "dc "
  printf 'y\n' >"$BATS_TEST_TMPDIR/tty"
  ff rollback
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "FreeFrame is back on 1.21.0"
}

@test "rollback: a missing backup is refused before anything changes" {
  failed_update
  rm "$UPDATE_BACKUP"
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_out "is not there any more"
  assert_not_called "dc "
}

@test "update: refuses while an earlier update's record stands, naming rollback and how to drop the record" {
  failed_update
  ff update
  [ "$status" -eq 1 ]
  assert_out "An earlier update did not finish"
  assert_out "sudo freeframe rollback"
  assert_out "sudo rm '$FF_ROOT/.update-undo'"
  assert_not_called "dc "
  assert_not_called fetch
}

@test "update: an older rollback copy is replaced only once the new update's backup worked" {
  mkdir -p "$FF_ROOT/rollback"
  printf 'older\n' >"$FF_ROOT/rollback/docker-compose.images.yml"
  STUB_PG_DOWN=1 FF_BACKUP_DB_WAIT=10 ff update
  [ "$status" -eq 1 ]
  assert_eq "$(cat "$FF_ROOT/rollback/docker-compose.images.yml")" older
  [ ! -e "$FF_ROOT/.update-undo" ]
}

@test "backup: the backup a rollback needs is never pruned while the record stands" {
  failed_update
  local i
  mkdir -p "$BK"
  for i in 1 2 3 4 5 6 7 8; do
    printf 'PGDMP later %s\n' "$i" >"$BK/freeframe-2099010$i-030000.dump"
  done
  ff backup
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ -f "$UPDATE_BACKUP" ]
}

@test "status: an unfinished update names the rollback" {
  failed_update
  ff status
  [ "$status" -eq 0 ]
  assert_out "An update did not finish. To go back to 1.21.0, run: sudo freeframe rollback"
}

@test "rollback: compose files that do not parse are no dead end: it stops by project label" {
  failed_update
  export STUB_CONFIG_FAIL_NEW=1
  ff rollback --yes
  [ "$status" -eq 0 ] || { echo "$output"; cat "$CALLS"; false; }
  grep -q "^docker ps --filter label=com.docker.compose.project=freeframe" "$CALLS"
  grep -qx "docker stop c1 c4" "$CALLS"
  assert_files_from_before
}

@test "rollback: a saved set without the main compose files is refused before anything changes" {
  failed_update
  rm "$FF_ROOT/rollback/docker-compose.images.yml"
  cp "$FF_ROOT/docker-compose.images.yml" "$BATS_TEST_TMPDIR/images.now"
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_out "incomplete"
  cmp "$BATS_TEST_TMPDIR/images.now" "$FF_ROOT/docker-compose.images.yml"
  assert_not_called "dc "
}

@test "rollback: install answers are refused, as update does" {
  failed_update
  ff rollback --yes --domain review.yourstudio.com
  [ "$status" -eq 2 ]
  assert_out "sudo freeframe configure"
  assert_not_called "dc "
}

@test "rollback: failure hints use sudo freeframe rollback, not a --yes form" {
  failed_update
  export STUB_RESTORE_FAIL=1
  ff rollback --yes
  [ "$status" -eq 1 ]
  assert_out "To try again, run: sudo freeframe rollback"
  assert_no_out "rollback --yes"
}
