#!/usr/bin/env bats
# `freeframe update` (spec goals 12 and 12b), with every side effect stubbed
# (manage_stubs.bash). Each flow runs in a fresh bash under the script's own
# strict mode.

load manage_helpers

setup() {
  manage_setup
  installed_server sslip bundled 1.21.0
  # The channel now has a newer release.
  remote_release 1.22.0
}

# A newer freeframe command on the channel: the script plus one line.
newer_command() { printf '\n# a newer freeframe command\n' >>"$STUB_REMOTE/deploy/freeframe"; }

newest_dump() { (cd "$BK" && LC_ALL=C ls freeframe-*.dump 2>/dev/null | tail -1); }

# ---------------------------------------------------------------- self-update

@test "update: re-execs exactly once when the downloaded command differs" {
  newer_command
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; cat "$CALLS"; false; }
  assert_eq "$(grep -c '^reexec ' "$CALLS")" 1
  assert_eq "$(grep -c '^fetch latest deploy/freeframe$' "$CALLS")" 1
  grep -qx "reexec $FF_BIN_DIR/freeframe update" "$CALLS"
  cmp "$STUB_REMOTE/deploy/freeframe" "$FF_BIN_DIR/freeframe"
  # The re-executed run did the update itself.
  assert_order "reexec " "dc exec -T postgres pg_dump" "dc up -d --no-build"
  assert_out "The freeframe command was updated"
}

@test "update: running the installed command itself, a newer download still re-execs once" {
  newer_command
  # As on a server: the script that runs is /usr/local/bin/freeframe, the
  # very file the update replaces.
  run bash -c 'set -Eeuo pipefail; source "$1"; source "$2"; shift 2; main "$@"' \
    ff-run "$FF_BIN_DIR/freeframe" "$STUBS" update
  [ "$status" -eq 0 ] || { echo "$output"; cat "$CALLS"; false; }
  assert_eq "$(grep -c '^reexec ' "$CALLS")" 1
  cmp "$STUB_REMOTE/deploy/freeframe" "$FF_BIN_DIR/freeframe"
}

@test "update: does not re-exec when the downloaded command is the same" {
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_not_called reexec
  assert_eq "$(grep -c '^fetch latest deploy/freeframe$' "$CALLS")" 1
  cmp "$FF_SCRIPT" "$FF_BIN_DIR/freeframe"
}

@test "update: with FF_SELF_UPDATED=1 the command is not downloaded again" {
  newer_command
  export FF_SELF_UPDATED=1
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_not_called "deploy/freeframe"
  assert_not_called reexec
  cmp "$FF_SCRIPT" "$FF_BIN_DIR/freeframe"
}

@test "update: the real re-exec runs the new command once, with the same options" {
  # A stand-in that carries the marks of a freeframe script.
  printf '#!/usr/bin/env bash\nFF_SCRIPT_KIND=freeframe-management-script\ncmd_update() { :; }\necho "new freeframe: FF_SELF_UPDATED=$FF_SELF_UPDATED args: $*"\n' \
    >"$STUB_REMOTE/deploy/freeframe"
  export STUB_REAL_REEXEC=1
  ff update --channel latest
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "new freeframe: FF_SELF_UPDATED=1 args: update --channel latest"
  cmp "$STUB_REMOTE/deploy/freeframe" "$FF_BIN_DIR/freeframe"
  [ "$(stat -c %a "$FF_BIN_DIR/freeframe")" = 755 ]
  # The old process stopped at the exec: nothing else ran.
  assert_not_called "dc "
}

@test "update: a cut-off command download is refused and nothing changes" {
  printf '#!/usr/bin/env bash\nif true; then\n' >"$STUB_REMOTE/deploy/freeframe"
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  ff update
  [ "$status" -eq 1 ]
  assert_out "nothing was changed"
  cmp "$FF_SCRIPT" "$FF_BIN_DIR/freeframe"
  cmp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  assert_not_called "dc "
  [ ! -d "$BK" ]
}

@test "update: a download that is valid bash but not the freeframe script is refused" {
  printf '#!/usr/bin/env bash\necho "some other script"\n' >"$STUB_REMOTE/deploy/freeframe"
  ff update
  [ "$status" -eq 1 ]
  assert_out "is not the freeframe command"
  assert_out "nothing was changed"
  cmp "$FF_SCRIPT" "$FF_BIN_DIR/freeframe"
  assert_not_called "dc "
  # With the marker but without the update command, it is refused too.
  printf '#!/usr/bin/env bash\nFF_SCRIPT_KIND=freeframe-management-script\n' >"$STUB_REMOTE/deploy/freeframe"
  ff update
  [ "$status" -eq 1 ]
  cmp "$FF_SCRIPT" "$FF_BIN_DIR/freeframe"
}

@test "update: a failed command download stops before anything changes" {
  export STUB_FETCH_FAIL=deploy/freeframe
  ff update
  [ "$status" -eq 1 ]
  assert_out "nothing was changed"
  assert_not_called "dc "
  assert_not_called "fetch latest docker-compose"
}

# ---------------------------------------------------------------- the update

@test "update: backs up, downloads, refreshes the timer, merges, pulls, starts and checks, in order" {
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "fetch latest deploy/freeframe" "dc exec -T postgres pg_dump -Fc" \
    "fetch latest docker-compose.prod.yml" "fetch latest docker-compose.images.yml" \
    "fetch latest docker-compose.https.yml" "fetch latest docker-compose.storage.yml" \
    "fetch latest deploy/systemd/freeframe-backup.service" "fetch latest deploy/systemd/freeframe-backup.timer" \
    install_backup_timer derive_env "dc pull" "dc up -d --no-build" "localhost:8000/health" \
    "probe_health sslip 203-0-113-7.sslip.io"
  # The units the timer is refreshed from are the channel's new ones.
  cmp "$STUB_REMOTE/deploy/systemd/freeframe-backup.service" "$FF_ROOT/systemd/freeframe-backup.service"
  cmp "$STUB_REMOTE/docker-compose.images.yml" "$FF_ROOT/docker-compose.images.yml"
}

@test "update: prints the version before and after, and the backup it took" {
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "FreeFrame was updated from 1.21.0 to 1.22.0."
  local d
  d=$(newest_dump)
  [ -n "$d" ]
  assert_out "$BK/$d"
}

@test "update: the same version says it is up to date" {
  remote_release 1.21.0
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "already on the newest release of the latest channel (1.21.0)"
}

@test "update: keeps every existing key and adds the ones a new release needs" {
  # Settings from an older release: two keys the current one writes are missing.
  grep -v -e '^MAINTENANCE_CONCURRENCY=' -e '^MAIL_PROVIDER=' "$ENV" >"$ENV.tmp"
  mv "$ENV.tmp" "$ENV"
  chmod 600 "$ENV"
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  # Every line that was there is still there, unchanged.
  local line
  while IFS= read -r line; do
    grep -qxF -- "$line" "$ENV" || { echo "lost or changed: $line"; false; }
  done <"$BATS_TEST_TMPDIR/env.before"
  assert_has "$ENV" "MAINTENANCE_CONCURRENCY=1"
  assert_has "$ENV" "MAIL_PROVIDER=smtp"
  assert_secrets_same "$BATS_TEST_TMPDIR/env.before" "$ENV"
  # The file was backed up before the keys were added, and nothing pins the version.
  cmp "$FF_ROOT/$(env_backups | tail -1)" "$BATS_TEST_TMPDIR/env.before"
  assert_no_key "$ENV" FREEFRAME_VERSION
  [ "$(stat -c %a "$ENV")" = 600 ]
}

@test "update: a complete settings file is left byte for byte" {
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  cmp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  [ -z "$(env_backups)" ]
}

@test "update: no answers from the command line reach the settings" {
  export FREEFRAME_SMTP_HOST=smtp.example.com
  ff update
  [ "$status" -eq 2 ]
  assert_out "sudo freeframe configure"
  assert_not_called "dc "
}

# ---------------------------------------------------------------- failures

@test "update: FreeFrame not healthy afterwards prints the way back and exits 1" {
  export STUB_API_DOWN=1 FF_API_WAIT=10
  ff update
  [ "$status" -eq 1 ]
  local d
  d=$(newest_dump)
  [ -n "$d" ]
  assert_out "FreeFrame did not come back after the update to 1.22.0."
  assert_out "printf '\\nFREEFRAME_VERSION=1.21.0\\n' | sudo tee -a '$ENV' >/dev/null && sudo freeframe restore --yes '$BK/$d'"
  assert_out "sudo freeframe logs"
  assert_out "install.log"
}

@test "update: the printed way back pins the old version in a way compose and env_get read" {
  export STUB_API_DOWN=1 FF_API_WAIT=10
  ff update
  [ "$status" -eq 1 ]
  # Run the pin half of the printed line against the real file (no sudo here).
  local pin
  pin=$(grep -o "printf '.*' | sudo tee -a '[^']*'" <<<"$output" | head -1)
  [ -n "$pin" ]
  pin=${pin//sudo /}
  printf 'NO_NEWLINE_AT_END=x' >>"$ENV"
  eval "$pin" >/dev/null
  assert_eq "$(env_get "$ENV" FREEFRAME_VERSION)" 1.21.0
  assert_eq "$(env_get "$ENV" NO_NEWLINE_AT_END)" x
}

@test "update: the stack not starting prints the way back too" {
  export STUB_UP_FAIL=1
  ff update
  [ "$status" -eq 1 ]
  assert_out "sudo freeframe restore --yes '$BK/$(newest_dump)'"
  assert_out "FREEFRAME_VERSION=1.21.0"
}

@test "update: no answer through the web address prints the way back" {
  export STUB_HEALTH_FAIL=1 FF_HEALTH_WAIT=10
  ff update
  [ "$status" -eq 1 ]
  assert_out "sudo freeframe restore --yes '$BK/$(newest_dump)'"
}

@test "update: a failed image download leaves FreeFrame on the version it had" {
  export STUB_PULL_FAIL=1
  ff update
  [ "$status" -eq 1 ]
  assert_out "could not be downloaded"
  assert_out "still running the version it had (1.21.0)"
  assert_not_called "dc up -d"
}

@test "update: a backup that fails stops it before anything is downloaded" {
  export STUB_PG_DOWN=1 FF_BACKUP_DB_WAIT=10
  ff update
  [ "$status" -eq 1 ]
  assert_out "No new backup was made"
  assert_out "The update stopped before changing anything"
  assert_not_called "fetch latest docker-compose"
  assert_not_called "dc pull"
  cmp "$FF_ROOT/docker-compose.images.yml" <(sed 's/1.22.0/1.21.0/g' "$STUB_REMOTE/docker-compose.images.yml")
}

@test "update: a failed setup file download replaces none of them" {
  export STUB_FETCH_FAIL=docker-compose.storage.yml
  cp "$FF_ROOT/docker-compose.images.yml" "$BATS_TEST_TMPDIR/images.before"
  ff update
  [ "$status" -eq 1 ]
  assert_out "Could not download docker-compose.storage.yml"
  cmp "$FF_ROOT/docker-compose.images.yml" "$BATS_TEST_TMPDIR/images.before"
  assert_not_called "dc pull"
  [ -z "$(ls -A "$FF_ROOT" | grep '^\.download')" ]
}

@test "update: a setup file that cannot be moved into place puts back the ones already moved" {
  export STUB_MV_FAIL=docker-compose.storage.yml
  local f
  mkdir -p "$BATS_TEST_TMPDIR/before"
  for f in docker-compose.prod.yml docker-compose.images.yml docker-compose.https.yml docker-compose.storage.yml; do
    cp "$FF_ROOT/$f" "$BATS_TEST_TMPDIR/before/$f"
  done
  ff update
  [ "$status" -eq 1 ]
  assert_out "could not be put in place, so none of the setup files was replaced"
  # The images file was moved in before the storage file failed: it is back.
  for f in docker-compose.prod.yml docker-compose.images.yml docker-compose.https.yml docker-compose.storage.yml; do
    cmp "$BATS_TEST_TMPDIR/before/$f" "$FF_ROOT/$f"
  done
  assert_not_called "dc pull"
  [ -z "$(ls -A "$FF_ROOT" | grep '^\.download')" ]
}

@test "update: a pinned version is kept and said" {
  printf 'FREEFRAME_VERSION=1.20.0\n' >>"$ENV"
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "pinned to version 1.20.0"
  assert_has "$ENV" "FREEFRAME_VERSION=1.20.0"
}

# ---------------------------------------------------------------- channel

@test "update: --channel wins, is saved after a backup, and the command and files come from it" {
  ff update --channel stable
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -qx "fetch stable deploy/freeframe" "$CALLS"
  grep -qx "fetch stable docker-compose.images.yml" "$CALLS"
  assert_not_called "fetch latest"
  assert_has "$ENV" "FREEFRAME_CHANNEL=stable"
  assert_has "$FF_ROOT/$(env_backups | head -1)" "FREEFRAME_CHANNEL=latest"
}

@test "update: without --channel the server's channel is used for the command and the files" {
  env_set "$ENV" FREEFRAME_CHANNEL stable
  ff update
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -qx "fetch stable deploy/freeframe" "$CALLS"
  grep -qx "fetch stable docker-compose.prod.yml" "$CALLS"
  assert_not_called "fetch latest"
}

@test "update: a channel that is not a branch or tag name is refused" {
  ff update --channel '../x'
  [ "$status" -eq 2 ]
  assert_not_called fetch
}

# ---------------------------------------------------------------- refusals

@test "update: not root, not installed, an unfinished install or restore, and the lock all refuse" {
  STUB_NOT_ROOT=1 ff update
  [ "$status" -eq 2 ]
  assert_out "sudo freeframe update"

  rm "$FF_ROOT/.installed"
  ff update
  [ "$status" -eq 1 ]
  assert_out "did not finish"
  assert_out "sudo freeframe install"
  printf 'FreeFrame installed\n' >"$FF_ROOT/.installed"

  mkdir -p "$BK"
  printf 'PGDMP old\n' >"$BK/freeframe-20261020-030000.dump"
  printf '%s\n' "$BK/freeframe-20261020-030000.dump" >"$FF_ROOT/.restore-undo"
  ff update
  [ "$status" -eq 1 ]
  assert_out "A restore of the database did not finish"
  assert_out "sudo freeframe restore --yes '$BK/freeframe-20261020-030000.dump'"
  rm "$FF_ROOT/.restore-undo"
  assert_not_called "dc "

  exec 9>>"$FF_ROOT/.freeframe.lock"
  flock -n 9
  ff update
  exec 9>&-
  [ "$status" -eq 1 ]
  assert_out "Another backup or restore is running"
  assert_not_called "dc "
}

@test "update: stray words are a usage error" {
  ff update now
  [ "$status" -eq 2 ]
  assert_not_called fetch
}
