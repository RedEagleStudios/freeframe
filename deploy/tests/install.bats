#!/usr/bin/env bats
# `freeframe install` end to end, with every side effect stubbed
# (install_stubs.bash), and the install.sh bootstrap.
#
# The flow runs in a fresh bash under the script's own strict mode, exactly
# as `sudo freeframe install` would, with the stubs sourced on top. Answers
# come from FREEFRAME_* variables, flags or a fake terminal file (FF_TTY).

load helpers

setup() {
  common_setup
  export STUB_DIR="$BATS_TEST_TMPDIR"
  export CALLS="$BATS_TEST_TMPDIR/calls"
  : >"$CALLS"
  STUBS="${BATS_TEST_DIRNAME}/install_stubs.bash"
  export FF_OS_RELEASE="$BATS_TEST_TMPDIR/os-release"
  os_release ubuntu 24.04 noble
  export FF_CORES=4 FF_RAM_MB=8192
  # Where `install` puts the freeframe command (never the real /usr/local/bin).
  export FF_BIN_DIR="$BATS_TEST_TMPDIR/bin-dir"
  ENV="$FF_ROOT/.env.prod"
}

os_release() {
  printf 'PRETTY_NAME="%s %s"\nID=%s\nVERSION_ID="%s"\nVERSION_CODENAME=%s\n' \
    "$1" "$2" "$1" "$2" "$3" >"$FF_OS_RELEASE"
}

# ff_install ARGS...: run `freeframe install ARGS` with the stubs.
ff_install() {
  run bash -c 'set -Eeuo pipefail; source "$1"; source "$2"; shift 2; main "$@"' \
    ff-install "$FF_SCRIPT" "$STUBS" install "$@"
}

# The answers of a typical unattended run (plain HTTP, bundled storage).
plain_answers() {
  export FREEFRAME_HTTPS=off
  export FREEFRAME_ADMIN_NAME="Ada Admin"
  export FREEFRAME_ADMIN_EMAIL=admin@example.com
  export FREEFRAME_ADMIN_PASSWORD=s3cret-pass
}

# assert_order PATTERN...: each fixed string appears in $CALLS, each on a
# later line than the previous one.
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

# FF_ROOT holds nothing at all.
assert_root_untouched() {
  local left
  left=$(ls -A "$FF_ROOT")
  if [[ -n $left ]]; then
    echo "expected $FF_ROOT to be empty, found: $left"
    return 1
  fi
}

# ---------------------------------------------------------------- happy path

@test "install: a fresh run does every step in order" {
  plain_answers
  ff_install --yes --channel latest
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order detect_foreign_install docker_status "net_reachable https://ghcr.io" \
    "net_reachable https://download.docker.com" install_docker \
    "fetch latest docker-compose.prod.yml" "fetch latest docker-compose.images.yml" \
    "fetch latest docker-compose.https.yml" "fetch latest docker-compose.storage.yml" \
    "fetch latest deploy/systemd/freeframe-backup.service" "fetch latest deploy/systemd/freeframe-backup.timer" \
    derive_env "dc pull" "dc config --services" "dc up -d" \
    "localhost:8000/health" "localhost:8000/setup/create-superadmin" \
    "dc up -d --no-build traefik" "probe_health off 203.0.113.7" install_backup_timer
  [ -f "$ENV" ]
  assert_has "$ENV" "FRONTEND_URL=http://203.0.113.7"
  assert_has "$ENV" "FREEFRAME_CHANNEL=latest"
  assert_out "FreeFrame is ready: http://203.0.113.7"
  assert_out "Sign in as admin@example.com"
}

@test "install: the first start leaves traefik out, and starts every other service" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  local first
  first=$(grep -m1 '^dc up -d' "$CALLS")
  [[ " $first " != *" traefik "* ]] || { echo "$first"; false; }
  local s
  for s in postgres redis api worker email_worker maintenance_worker beat web minio minio-init; do
    [[ " $first " == *" $s "* ]] || { echo "missing $s in: $first"; false; }
  done
}

@test "install: traefik starts only after the admin account is created" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  local admin traefik
  admin=$(grep -n create-superadmin "$CALLS" | cut -d: -f1)
  traefik=$(grep -n '^dc up -d --no-build traefik$' "$CALLS" | cut -d: -f1)
  [ -n "$admin" ] && [ -n "$traefik" ]
  ((traefik > admin))
}

@test "install: fails closed when the setup files have no api service" {
  plain_answers
  export STUB_SERVICES="traefik postgres redis web"
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "do not describe FreeFrame as expected"
  assert_not_called "dc up -d"
  assert_not_called create-superadmin
}

@test "install: fails closed when the setup files list no services" {
  plain_answers
  export STUB_SERVICES=""
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_not_called "dc up -d"
}

@test "install: a traefik left running by an earlier attempt is stopped before the admin call" {
  plain_answers
  export STUB_HEALTH_FAIL=1 FF_HEALTH_WAIT=10
  ff_install --yes
  [ "$status" -eq 1 ]
  unset STUB_HEALTH_FAIL
  : >"$CALLS"
  export STUB_OURS_RUNNING=1
  export STUB_SS='LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("docker-proxy",pid=4242,fd=7))'
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "dc up -d --no-build postgres" "dc ps -q traefik" "dc stop traefik" "dc ps -q traefik" \
    create-superadmin "dc up -d --no-build traefik"
}

@test "install: a traefik that will not stop stops the install before the admin call" {
  plain_answers
  export STUB_OURS_RUNNING=1 STUB_TRAEFIK_STUCK=1
  ff_install --yes
  [ "$status" -eq 1 ] || { echo "$output"; false; }
  assert_out "keep the setup page private"
  assert_not_called create-superadmin
}

@test "install: the admin answers go to the setup endpoint as JSON on stdin" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_eq "$(cat "$BATS_TEST_TMPDIR/superadmin.json")" \
    '{"email":"admin@example.com","name":"Ada Admin","password":"s3cret-pass"}'
  # The password never appears in a command line.
  assert_not_called "s3cret-pass"
  grep -q -- "dc exec -T api curl" "$CALLS"
}

@test "install: names and passwords with quotes and backslashes are escaped in the JSON" {
  plain_answers
  export FREEFRAME_ADMIN_NAME='Ada "The" Admin'
  export FREEFRAME_ADMIN_PASSWORD='pa\ss"word'
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(cat "$BATS_TEST_TMPDIR/superadmin.json")" \
    '{"email":"admin@example.com","name":"Ada \"The\" Admin","password":"pa\\ss\"word"}'
}

@test "install: docker 24 or newer with compose is used as is" {
  plain_answers
  export STUB_DOCKER=ok
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_not_called install_docker
  assert_not_called "net_reachable https://download.docker.com"
}

@test "install: FF_SKIP_PULL=1 skips dc pull" {
  plain_answers
  export FF_SKIP_PULL=1
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_not_called "dc pull"
  grep -q '^dc up -d' "$CALLS"
}

@test "install: the channel defaults to latest, the ref install.sh uses by default" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  grep -q '^fetch latest docker-compose.prod.yml$' "$CALLS"
  assert_has "$ENV" "FREEFRAME_CHANNEL=latest"
}

@test "install: a re-run with --channel takes the setup files from it and saves it, after a backup" {
  plain_answers
  ff_install --yes --channel latest
  [ "$status" -eq 0 ]
  # Settings from an earlier attempt that say stable.
  rm -f "$FF_ROOT/.installed"
  env_set "$ENV" FREEFRAME_CHANNEL stable
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  rm -f "$ENV".2*
  : >"$CALLS"
  # install.sh downloaded this script from latest and passes --channel latest.
  ff_install --yes --channel latest
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -qx 'fetch latest docker-compose.prod.yml' "$CALLS"
  assert_not_called "fetch stable"
  assert_has "$ENV" "FREEFRAME_CHANNEL=latest"
  local b
  b=$(ls "$ENV".2*)
  cmp "$b" "$BATS_TEST_TMPDIR/env.before"
}

@test "install: a re-run without --channel keeps the settings file's channel" {
  plain_answers
  ff_install --yes --channel stable
  [ "$status" -eq 0 ]
  rm -f "$FF_ROOT/.installed"
  : >"$CALLS"
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -qx 'fetch stable docker-compose.prod.yml' "$CALLS"
  assert_has "$ENV" "FREEFRAME_CHANNEL=stable"
}

@test "install: install.log is private and never holds the password or the tokens" {
  plain_answers
  unset FREEFRAME_ADMIN_PASSWORD
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  local log="$FF_ROOT/install.log" pw
  [ -f "$log" ]
  [ "$(stat -c %a "$log" 2>/dev/null || stat -f %Lp "$log")" = 600 ]
  pw=$(sed -n 's/.*"password":"\([^"]*\)".*/\1/p' "$BATS_TEST_TMPDIR/superadmin.json")
  [[ $pw =~ ^[0-9a-f]{24}$ ]] || { echo "generated: $pw"; false; }
  # Shown once on the terminal...
  assert_out "$pw"
  # ...but not in the log.
  # (A bare `! grep` never fails a bats test, so these are spelled out.)
  if grep -q -F "$pw" "$log"; then echo "password in the log"; false; fi
  if grep -q -F "tok-SECRET" "$log"; then echo "token in the log"; false; fi
  grep -q "FreeFrame is ready" "$log"
}

@test "install: the nightly backup timer is set up once FreeFrame answers, before the summary" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "localhost:8000/setup/create-superadmin" "dc up -d --no-build traefik" \
    "probe_health off" install_backup_timer
  [ "$(grep -c '^install_backup_timer$' "$CALLS")" -eq 1 ]
  [[ $output == *"nightly"*"FreeFrame is ready"* ]] || { echo "$output"; false; }
}

@test "install: the summary says the backups stay on this server and are nightly" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_out "every night at 3:00"
  assert_out "on this server only"
  assert_out "do not survive losing it"
}

@test "install: a backup timer that cannot be set up stops before marking the install complete" {
  plain_answers
  export STUB_TIMER_FAIL=1
  ff_install --yes
  [ "$status" -eq 1 ]
  [ ! -e "$FF_ROOT/.installed" ]
  assert_out "nightly database backups could not be set up"
  assert_out "FreeFrame is running"
  assert_out "install.log"
  # A re-run finishes the job.
  unset STUB_TIMER_FAIL
  ff_install --yes
  [ "$status" -eq 0 ]
  [ -f "$FF_ROOT/.installed" ]
}

@test "install: the backup units are downloaded into the systemd folder of the install" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  [ -f "$FF_ROOT/systemd/freeframe-backup.service" ]
  [ -f "$FF_ROOT/systemd/freeframe-backup.timer" ]
}

@test "install: marks the install complete only at the end" {
  plain_answers
  export STUB_HEALTH_FAIL=1 FF_HEALTH_WAIT=10
  ff_install --yes
  [ "$status" -eq 1 ]
  [ ! -e "$FF_ROOT/.installed" ]
  unset STUB_HEALTH_FAIL
  ff_install --yes
  [ "$status" -eq 0 ]
  [ -f "$FF_ROOT/.installed" ]
}

@test "install: an unexpected failure says so in plain words and names the log" {
  plain_answers
  # The completion marker cannot be written: a folder is in its way.
  mkdir "$FF_ROOT/.installed"
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "stopped unexpectedly"
  assert_out "install.log"
  assert_out "sudo freeframe install again"
}

@test "install: installs the freeframe command (mode 755) once it goes ahead" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  cmp "$FF_SCRIPT" "$FF_BIN_DIR/freeframe"
  [ "$(stat -c %a "$FF_BIN_DIR/freeframe" 2>/dev/null || stat -f %Lp "$FF_BIN_DIR/freeframe")" = 755 ]
}

@test "install: refused, cancelled and already-installed runs leave the command alone" {
  plain_answers
  os_release debian 11 bullseye
  ff_install --yes
  [ "$status" -eq 3 ]
  [ ! -e "$FF_BIN_DIR/freeframe" ]

  os_release ubuntu 24.04 noble
  export FF_RAM_MB=1024
  ff_install --yes
  [ "$status" -eq 3 ]
  [ ! -e "$FF_BIN_DIR/freeframe" ]
  export FF_RAM_MB=8192

  unset FREEFRAME_ADMIN_EMAIL
  ff_install --yes
  [ "$status" -eq 2 ]
  [ ! -e "$FF_BIN_DIR/freeframe" ]
  export FREEFRAME_ADMIN_EMAIL=admin@example.com

  unset FREEFRAME_HTTPS FREEFRAME_ADMIN_NAME FREEFRAME_ADMIN_EMAIL FREEFRAME_ADMIN_PASSWORD
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "n" >"$BATS_TEST_TMPDIR/tty"
  FF_TTY="$BATS_TEST_TMPDIR/tty" ff_install
  [ "$status" -eq 1 ] || { echo "$output"; false; }
  assert_out "Install cancelled"
  [ ! -e "$FF_BIN_DIR/freeframe" ]

  plain_answers
  mkdir -p "$FF_BIN_DIR"
  printf 'old copy\n' >"$FF_BIN_DIR/freeframe"
  : >"$ENV"
  : >"$FF_ROOT/.installed"
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_eq "$(cat "$FF_BIN_DIR/freeframe")" "old copy"
}

# ---------------------------------------------------------------- re-runs

@test "install: a second run changes nothing and points at freeframe update" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  : >"$CALLS"
  ff_install --yes --domain other.example.com --https own
  [ "$status" -eq 0 ]
  cmp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  assert_out "already installed"
  assert_out "sudo freeframe update"
  assert_not_called fetch
  assert_not_called derive_env
  assert_not_called "dc "
}

@test "install: settings copied from another server are kept byte for byte" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  # A new server: only the settings file came across.
  rm -f "$FF_ROOT/.installed" "$FF_ROOT"/docker-compose.*.yml "$FF_ROOT/install.log"
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  : >"$CALLS"
  export FREEFRAME_HTTPS=sslip
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  cmp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  grep -q derive_env "$CALLS"
  grep -q create-superadmin "$CALLS"
  assert_out "from the existing settings"
}

@test "install: a complete settings file copied in as 644 ends up 600" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  rm -f "$FF_ROOT/.installed"
  chmod 644 "$ENV"
  cp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  cmp "$ENV" "$BATS_TEST_TMPDIR/env.before"
  [ "$(stat -c %a "$ENV" 2>/dev/null || stat -f %Lp "$ENV")" = 600 ]
}

@test "install: an admin that already exists is not an error" {
  plain_answers
  export STUB_ADMIN_CODE=403 STUB_ADMIN_BODY='{"detail":"Setup already completed. Superadmin already exists."}'
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "An admin account already exists"
  grep -q "dc up -d --no-build traefik" "$CALLS"
}

@test "install: a refused admin account stops before traefik with the reason" {
  plain_answers
  export STUB_ADMIN_CODE=422 STUB_ADMIN_BODY='{"detail":[{"msg":"value is not a valid email address"}]}'
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "value is not a valid email address"
  assert_not_called "dc up -d --no-build traefik"
}

@test "install: a generated password reaches the terminal even when a later step fails" {
  plain_answers
  unset FREEFRAME_ADMIN_PASSWORD
  export STUB_TRAEFIK_FAIL=1
  ff_install --yes
  [ "$status" -eq 1 ]
  local pw log="$FF_ROOT/install.log"
  pw=$(sed -n 's/.*"password":"\([^"]*\)".*/\1/p' "$BATS_TEST_TMPDIR/superadmin.json")
  [[ $pw =~ ^[0-9a-f]{24}$ ]] || { echo "generated: $pw"; false; }
  assert_out "Traefik"
  # Shown right after the account was made, and again with the failure.
  [ "$(grep -c -F "$pw" <<<"$output")" -ge 2 ] || { echo "$output"; false; }
  if grep -q -F "$pw" "$log"; then echo "password in the log"; false; fi
}

@test "install: a typed password is never echoed back when a later step fails" {
  plain_answers
  export STUB_TRAEFIK_FAIL=1
  ff_install --yes
  [ "$status" -eq 1 ]
  if grep -q -F "s3cret-pass" <<<"$output"; then echo "$output"; false; fi
}

@test "install: the API not reachable for the admin call says so, not a bad password" {
  plain_answers
  export STUB_ADMIN_EXEC_FAIL=1
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "API could not be reached"
  assert_out "install.log"
  if grep -q "email and password" <<<"$output"; then echo "$output"; false; fi
  assert_not_called "dc up -d --no-build traefik"
}

@test "install: too many admin tries (429) says to wait 10 minutes" {
  plain_answers
  export STUB_ADMIN_CODE=429 STUB_ADMIN_BODY='{"detail":"Too many requests"}'
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "wait 10 minutes"
  assert_not_called "dc up -d --no-build traefik"
}

@test "install: the API never answering stops before the admin and traefik" {
  plain_answers
  export STUB_API_DOWN=1 FF_API_WAIT=20
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_not_called create-superadmin
  assert_not_called "dc up -d --no-build traefik"
  assert_out "install.log"
  assert_out "sudo freeframe install"
}

# ---------------------------------------------------------------- refusals

@test "install: not root exits 2 and changes nothing" {
  plain_answers
  export STUB_NOT_ROOT=1
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_out "as root"
  assert_root_untouched
  [ ! -s "$CALLS" ]
}

@test "install: an unsupported OS exits 3 before any change" {
  plain_answers
  os_release debian 11 bullseye
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "not supported yet"
  assert_out "debian 11"
  assert_root_untouched
  [ ! -s "$CALLS" ]
}

@test "install: no os-release at all is unsupported" {
  plain_answers
  rm -f "$FF_OS_RELEASE"
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_root_untouched
}

@test "install: an unsupported processor exits 3 before any change" {
  plain_answers
  export STUB_ARCH=armv7l
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "armv7l"
  assert_root_untouched
  [ ! -s "$CALLS" ]
}

@test "install: a foreign install exits 3 before any fetch and writes nothing" {
  plain_answers
  export STUB_FOREIGN=0
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "set up by hand"
  assert_out "will not touch it"
  assert_not_called fetch
  assert_not_called install_docker
  assert_root_untouched
}

@test "install: docker not answering is a failure, never read as no FreeFrame" {
  plain_answers
  export STUB_FOREIGN=2
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_not_called fetch
  assert_root_untouched
}

@test "install: snap docker is refused with instructions" {
  plain_answers
  export STUB_DOCKER=snap
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "snap"
  assert_not_called install_docker
  assert_not_called fetch
}

@test "install: an old docker is refused" {
  plain_answers
  export STUB_DOCKER="old 20.10.24"
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "20.10.24"
  assert_not_called install_docker
}

@test "install: too little memory is refused, 2 to 4 GB only warns" {
  plain_answers
  export FF_RAM_MB=1024
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "memory"
  assert_not_called install_docker

  export FF_RAM_MB=3900
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "4 GB"
}

@test "install: no outbound access to ghcr.io stops before any change" {
  plain_answers
  export STUB_NET_DOWN=ghcr.io
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "ghcr.io"
  assert_not_called install_docker
  assert_not_called fetch
}

@test "install: low disk space warns" {
  plain_answers
  export STUB_FREE_MB=10000
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_out "free disk space"
}

@test "install: a refusal after the log exists names the log and does not claim nothing changed" {
  plain_answers
  export FF_RAM_MB=1024
  ff_install --yes
  [ "$status" -eq 3 ]
  assert_out "$FF_ROOT/install.log"
  if grep -qi "nothing was changed" <<<"$output"; then echo "$output"; false; fi
}

@test "install: an answer error after the log exists names the log" {
  plain_answers
  unset FREEFRAME_ADMIN_EMAIL
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_out "$FF_ROOT/install.log"
  assert_out "set FREEFRAME_ADMIN_EMAIL (the admin account's email)"
}

@test "install: a missing answer is named without the prompt's indent" {
  plain_answers
  export FREEFRAME_STORAGE=s3 FREEFRAME_S3_ACCESS_KEY=a FREEFRAME_S3_SECRET_KEY=b
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_out "Missing answer: set FREEFRAME_S3_BUCKET (Bucket name)"
}

@test "install: the endpoint message matches what is accepted" {
  plain_answers
  export FREEFRAME_STORAGE=s3 FREEFRAME_S3_BUCKET=footage FREEFRAME_S3_ACCESS_KEY=a \
    FREEFRAME_S3_SECRET_KEY=b FREEFRAME_S3_ENDPOINT=s3.example.com
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_out "starting with https:// (or http://"
  export FREEFRAME_S3_ENDPOINT=http://nas.local:9000
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "install: cancelling after stopping a program says which one, and how to start it again" {
  printf '%s\n' "y" "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "n" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  export STUB_SS='LISTEN 0 511 [::]:443 [::]:* users:(("apache2",pid=900,fd=4))'
  export STUB_UNIT=apache2.service STUB_SS_CLEARS=1
  ff_install
  [ "$status" -eq 1 ]
  assert_out "Install cancelled. Nothing was installed."
  assert_out "stopped apache2"
  assert_out "sudo systemctl enable --now apache2.service"
  assert_out "install.log"
}

@test "install: an existing stricter FreeFrame folder keeps its mode; a new one is 755" {
  plain_answers
  chmod 700 "$FF_ROOT"
  ff_install --yes
  [ "$status" -eq 0 ]
  [ "$(stat -c %a "$FF_ROOT" 2>/dev/null || stat -f %Lp "$FF_ROOT")" = 700 ]

  export FF_ROOT="$BATS_TEST_TMPDIR/fresh/freeframe"
  ff_install --yes
  [ "$status" -eq 0 ]
  [ "$(stat -c %a "$FF_ROOT" 2>/dev/null || stat -f %Lp "$FF_ROOT")" = 755 ]
}

# ---------------------------------------------------------------- answers

@test "install: with no terminal and no --yes it stops and names --yes" {
  plain_answers
  ff_install
  [ "$status" -eq 2 ]
  assert_out "--yes"
  assert_not_called install_docker
  assert_not_called fetch
}

@test "install: with no terminal a missing admin email names the variable" {
  plain_answers
  unset FREEFRAME_ADMIN_EMAIL
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_out "FREEFRAME_ADMIN_EMAIL"
  assert_not_called install_docker
  assert_not_called fetch
  [ ! -e "$ENV" ]
}

@test "install: a bad web address given ahead of time exits 2 before any change" {
  plain_answers
  unset FREEFRAME_HTTPS
  ff_install --yes --domain 'https://review.example.com:8443/x y'
  [ "$status" -eq 2 ]
  assert_not_called install_docker
  [ ! -e "$ENV" ]
}

@test "install: a pasted https:// and trailing slash are forgiven" {
  plain_answers
  unset FREEFRAME_HTTPS
  export FREEFRAME_ACME_EMAIL=certs@example.com
  ff_install --yes --domain 'https://Review.Example.com/'
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://review.example.com"
}

@test "install: a too-short admin password given ahead of time exits 2" {
  plain_answers
  export FREEFRAME_ADMIN_PASSWORD=short
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_out "8"
  assert_not_called install_docker
}

@test "install: HTTPS needs a certificate email; the admin email is the default" {
  plain_answers
  unset FREEFRAME_HTTPS
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=sslip"
  assert_has "$ENV" "ACME_EMAIL=admin@example.com"
}

@test "install: own S3 bucket answers from the environment" {
  plain_answers
  export FREEFRAME_STORAGE=s3 FREEFRAME_S3_BUCKET=footage FREEFRAME_S3_ACCESS_KEY=AKIAX \
    FREEFRAME_S3_SECRET_KEY='se$cret' FREEFRAME_S3_REGION=eu-west-1
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "S3_STORAGE=s3"
  assert_eq "$(env_get "$ENV" S3_SECRET_KEY)" 'se$cret'
  # AWS does not get CORS set automatically: the rules are printed.
  assert_out "AllowedOrigins"
  assert_out "http://203.0.113.7"
  local first
  first=$(grep -m1 '^dc up -d' "$CALLS")
  [[ " $first " != *" minio "* ]]
}

@test "install: skipping email says plainly what does not work" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_out "forgotten password"
  assert_has "$ENV" "MAIL_PROVIDER=smtp"
}

@test "install: interactive answers, hidden passwords typed twice" {
  # Enter (automatic address), certificate email, storage 1, skip email,
  # name, email, password twice, then confirm.
  printf '%s\n' "" "certs@example.com" "1" "" "Grace Hopper" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"
  assert_has "$ENV" "ACME_EMAIL=certs@example.com"
  assert_has "$ENV" "FREEFRAME_STORAGE=bundled"
  assert_eq "$(cat "$BATS_TEST_TMPDIR/superadmin.json")" \
    '{"email":"grace@example.com","name":"Grace Hopper","password":"long-password-1"}'
  assert_out "temporary"
  assert_out "Install now?"
}

@test "install: interactive passwords that differ are asked again" {
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-2" "long-password-3" "long-password-3" "y" \
    >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "did not match"
  grep -q '"password":"long-password-3"' "$BATS_TEST_TMPDIR/superadmin.json"
}

@test "install: interactive Enter at the password makes one up" {
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" "" "y" \
    >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -Eq '"password":"[0-9a-f]{24}"' "$BATS_TEST_TMPDIR/superadmin.json"
  assert_out "write it down"
}

@test "install: answering no at Install now changes nothing" {
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "n" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff_install
  [ "$status" -eq 1 ]
  assert_out "Nothing was installed"
  assert_not_called install_docker
  assert_not_called fetch
  [ ! -e "$ENV" ]
}

@test "install: interactive SMTP answers" {
  printf '%s\n' "" "certs@example.com" "1" "smtp.example.com" "465" "mailer@example.com" \
    "mail-pass" "" "Grace" "grace@example.com" "long-password-1" "long-password-1" "y" \
    >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "SMTP_HOST=smtp.example.com"
  assert_has "$ENV" "SMTP_PORT=465"
  assert_has "$ENV" "SMTP_USE_TLS=false"
  assert_has "$ENV" "SMTP_PASSWORD=mail-pass"
  assert_has "$ENV" "MAIL_FROM_ADDRESS=mailer@example.com"
}

@test "install: typing the server's IP at the address question means plain HTTP" {
  printf '%s\n' "10.0.0.5" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty" STUB_LOCAL_IP=10.0.0.5
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=http://10.0.0.5"
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
  assert_has "$ENV" "S3_PUBLIC_ENDPOINT=http://10.0.0.5:9000"
}

@test "install: no public IP means plain HTTP on the server's address" {
  plain_answers
  unset FREEFRAME_HTTPS
  export STUB_PUBLIC_IP="" STUB_LOCAL_IP=192.168.1.20
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=http://192.168.1.20"
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
}

@test "NAT ruling: a public VPS uses the automatic address, asked or unattended" {
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  FF_TTY="$BATS_TEST_TMPDIR/tty" ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"
  if grep -q "private address" <<<"$output"; then echo "$output"; false; fi

  rm -rf "$FF_ROOT" && mkdir -p "$FF_ROOT"
  plain_answers
  unset FREEFRAME_HTTPS
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"
}

@test "NAT ruling: behind NAT, Enter still means the automatic address, with a note" {
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty" STUB_LOCAL_IP=10.0.0.5
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "This server reaches the internet through a router (its own address is 10.0.0.5)."
  assert_out "choose 'this network only'"
  assert_out "type: local"
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"
}

@test "NAT ruling: behind NAT, 'this network only' means HTTP on the interface address" {
  printf '%s\n' "local" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty" STUB_LOCAL_IP=10.0.0.5
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=http://10.0.0.5"
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
  assert_has "$ENV" "S3_PUBLIC_ENDPOINT=http://10.0.0.5:9000"
  assert_out "FreeFrame is ready: http://10.0.0.5"
}

@test "NAT ruling: without NAT the address question has no router note" {
  printf '%s\n' "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  FF_TTY="$BATS_TEST_TMPDIR/tty" ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  if grep -q "through a router" <<<"$output"; then echo "$output"; false; fi
}

@test "NAT ruling: behind NAT and unattended, the default is still the automatic address" {
  plain_answers
  unset FREEFRAME_HTTPS
  export STUB_LOCAL_IP=10.0.0.5
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"
  assert_has "$ENV" "FREEFRAME_HTTPS=sslip"
}

@test "NAT ruling: behind NAT, an sslip certificate timeout falls back to HTTP on the interface address" {
  plain_answers
  unset FREEFRAME_HTTPS
  export STUB_LOCAL_IP=10.0.0.5 STUB_CERT_FAIL=1 FF_CERT_WAIT=10
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
  assert_has "$ENV" "FRONTEND_URL=http://10.0.0.5"
  assert_has "$ENV" "S3_PUBLIC_ENDPOINT=http://10.0.0.5:9000"
  assert_out "the server's own network address, http://10.0.0.5"
  assert_out "FreeFrame is ready: http://10.0.0.5"
  grep -q "probe_health off 10.0.0.5" "$CALLS"
}

@test "NAT ruling: explicit choices win behind NAT" {
  plain_answers
  export STUB_LOCAL_IP=10.0.0.5
  # plain_answers sets FREEFRAME_HTTPS=off: plain HTTP on the interface address.
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=http://10.0.0.5"

  rm -rf "$FF_ROOT" && mkdir -p "$FF_ROOT"
  ff_install --yes --https sslip
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"

  rm -rf "$FF_ROOT" && mkdir -p "$FF_ROOT"
  unset FREEFRAME_HTTPS
  export FREEFRAME_ACME_EMAIL=certs@example.com
  ff_install --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://review.yourstudio.com"
  # The DNS records point at the public address, not the private one.
  grep -q "dns_points_here review.yourstudio.com 203.0.113.7" "$CALLS"
}

@test "NAT ruling: without NAT the sslip fallback stays on the public address" {
  plain_answers
  export FREEFRAME_HTTPS=sslip STUB_CERT_FAIL=1 FF_CERT_WAIT=10
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=http://203.0.113.7"
}

# ---------------------------------------------------------------- server fixes

@test "install: adds swap only when memory is under 4 GB and there is none" {
  plain_answers
  export FF_RAM_MB=3900
  ff_install --yes
  [ "$status" -eq 0 ]
  grep -q '^add_swap$' "$CALLS"

  : >"$CALLS"
  rm -f "$FF_ROOT/.installed"
  export STUB_SWAP=1
  ff_install --yes
  assert_not_called add_swap

  : >"$CALLS"
  rm -f "$FF_ROOT/.installed"
  unset STUB_SWAP
  export FF_RAM_MB=8192
  ff_install --yes
  assert_not_called add_swap
}

@test "install: ufw gets allow rules only when it is active" {
  plain_answers
  ff_install --yes
  [ "$status" -eq 0 ]
  assert_not_called "ufw "

  : >"$CALLS"
  rm -f "$FF_ROOT/.installed"
  export STUB_UFW_ACTIVE=1
  ff_install --yes
  [ "$status" -eq 0 ]
  grep -q '^ufw allow 80/tcp$' "$CALLS"
  grep -q '^ufw allow 443/tcp$' "$CALLS"
  assert_not_called "ufw enable"
}

@test "install: a program on port 80 is named, and --yes does not stop it" {
  plain_answers
  export STUB_SS='LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=812,fd=6),("nginx",pid=813,fd=6))'
  export STUB_UNIT=nginx.service
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "Port 80 is used by nginx"
  assert_not_called "systemctl disable"
  assert_not_called install_docker
}

@test "install: interactive yes stops the program holding port 443" {
  printf '%s\n' "y" "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  export STUB_SS='LISTEN 0 511 [::]:443 [::]:* users:(("apache2",pid=900,fd=4))'
  export STUB_UNIT=apache2.service STUB_SS_CLEARS=1
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -q '^systemctl disable --now apache2.service$' "$CALLS"
}

@test "install: our own traefik on 80 from an earlier attempt is not in the way" {
  plain_answers
  export STUB_HEALTH_FAIL=1 FF_HEALTH_WAIT=10
  ff_install --yes
  [ "$status" -eq 1 ]
  unset STUB_HEALTH_FAIL
  export STUB_SS='LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("docker-proxy",pid=4242,fd=7))'
  export STUB_OURS_RUNNING=1
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "install: another Docker app on port 80 stops the install" {
  plain_answers
  export STUB_SS='LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:(("docker-proxy",pid=4242,fd=7))'
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "Docker"
}

# ---------------------------------------------------------------- DNS and HTTPS

@test "install: own domain prints the records and waits until they point here" {
  plain_answers
  unset FREEFRAME_HTTPS
  export FREEFRAME_ACME_EMAIL=certs@example.com STUB_DNS_FAILS=3
  ff_install --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "A record"
  assert_out "review.yourstudio.com"
  assert_out "storage.review.yourstudio.com"
  assert_out "203.0.113.7"
  assert_order "dns_points_here review.yourstudio.com 203.0.113.7" "sleep 15" \
    "dns_points_here review.yourstudio.com 203.0.113.7" "fetch latest docker-compose.prod.yml"
  assert_has "$ENV" "FRONTEND_URL=https://review.yourstudio.com"
  assert_has "$ENV" "FREEFRAME_HTTPS=own"
  grep -q "cert_issued review.yourstudio.com" "$CALLS"
  grep -q "cert_issued storage.review.yourstudio.com" "$CALLS"
}

@test "install: DNS that never points here stops an unattended run before any fetch" {
  plain_answers
  unset FREEFRAME_HTTPS
  export FREEFRAME_ACME_EMAIL=certs@example.com STUB_DNS_FAILS=1000 FF_DNS_WAIT=60
  ff_install --yes --domain review.yourstudio.com
  [ "$status" -eq 1 ]
  assert_out "FREEFRAME_HTTPS=sslip"
  assert_not_called fetch
}

@test "install: at the DNS wait, s switches to the automatic address" {
  printf '%s\n' "review.yourstudio.com" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" "s" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty" STUB_DNS_FAILS=1000
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://203-0-113-7.sslip.io"
  assert_has "$ENV" "FREEFRAME_HTTPS=sslip"
}

@test "install: at the DNS wait, c continues anyway with the own domain" {
  printf '%s\n' "review.yourstudio.com" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" "c" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty" STUB_DNS_FAILS=1000
  ff_install
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=https://review.yourstudio.com"
}

@test "install: sslip certificate that never arrives falls back to plain HTTP" {
  plain_answers
  export FREEFRAME_HTTPS=sslip STUB_CERT_FAIL=1 FF_CERT_WAIT=30
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
  assert_has "$ENV" "FRONTEND_URL=http://203.0.113.7"
  assert_has "$ENV" "S3_PUBLIC_ENDPOINT=http://203.0.113.7:9000"
  assert_has "$ENV" "STORAGE_ROUTED=false"
  assert_has "$ENV" "STORAGE_PORT_BIND=0.0.0.0:9000"
  assert_out "plain HTTP"
  assert_out "FreeFrame is ready: http://203.0.113.7"
  # The old file was backed up, and no secret changed.
  local backup
  backup=$(ls "$ENV".2* | head -1)
  [ -f "$backup" ]
  local k
  for k in POSTGRES_PASSWORD REDIS_PASSWORD JWT_SECRET S3_ACCESS_KEY S3_SECRET_KEY DATABASE_URL; do
    assert_eq "$(env_get "$ENV" "$k")" "$(env_get "$backup" "$k")"
  done
  grep -q -- "--force-recreate" "$CALLS"
  assert_order "cert_issued 203-0-113-7.sslip.io" "dc up -d --no-build --force-recreate" "probe_health off 203.0.113.7"
}

@test "install: sslip certificate issued keeps HTTPS" {
  plain_answers
  export FREEFRAME_HTTPS=sslip
  ff_install --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=sslip"
  assert_out "FreeFrame is ready: https://203-0-113-7.sslip.io"
  assert_out "temporary"
}

@test "install: own domain certificate that does not arrive is reported, not hidden" {
  plain_answers
  unset FREEFRAME_HTTPS
  export FREEFRAME_ACME_EMAIL=certs@example.com STUB_CERT_FAIL=1 FF_CERT_WAIT=30
  ff_install --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=own"
  assert_out "certificate"
  assert_out "has not arrived"
}

@test "install: sslip mode without a public IP exits 2" {
  plain_answers
  export FREEFRAME_HTTPS=sslip STUB_PUBLIC_IP="" STUB_LOCAL_IP=192.168.1.20
  ff_install --yes
  [ "$status" -eq 2 ]
  assert_not_called install_docker
}

@test "install: a failed download names the file and stops before derive" {
  plain_answers
  export STUB_FETCH_FAIL=docker-compose.https.yml
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "docker-compose.https.yml"
  assert_not_called derive_env
}

@test "install: a failed download on latest does not suggest latest; on another channel it does" {
  plain_answers
  export STUB_FETCH_FAIL=docker-compose.https.yml
  ff_install --yes --channel latest
  [ "$status" -eq 1 ]
  assert_out "a release after v1.21.0"
  if [[ $output == *"try --channel latest"* ]]; then echo "$output"; false; fi
  ff_install --yes --channel stable
  [ "$status" -eq 1 ]
  assert_out "try --channel latest"
}

# ---------------------------------------------------------------- real helpers

@test "fetch copies from FF_FETCH_FROM_DIR instead of downloading" {
  mkdir -p "$BATS_TEST_TMPDIR/src"
  printf 'services:\n  api: {}\n' >"$BATS_TEST_TMPDIR/src/docker-compose.prod.yml"
  FF_FETCH_FROM_DIR="$BATS_TEST_TMPDIR/src"
  curl() { echo "curl must not run" >&2; return 99; }
  fetch latest docker-compose.prod.yml "$FF_ROOT/docker-compose.prod.yml"
  cmp "$BATS_TEST_TMPDIR/src/docker-compose.prod.yml" "$FF_ROOT/docker-compose.prod.yml"
}

@test "fetch downloads the file from the ref on GitHub" {
  curl() {
    local out="" a
    while (($#)); do
      a=$1
      shift
      case $a in
        -o) out=$1; shift ;;
        https://*) printf '%s\n' "$a" >"$BATS_TEST_TMPDIR/url" ;;
      esac
    done
    printf 'downloaded\n' >"$out"
  }
  fetch latest deploy/freeframe "$FF_ROOT/x"
  assert_eq "$(cat "$BATS_TEST_TMPDIR/url")" \
    "https://raw.githubusercontent.com/Techiebutler/freeframe/latest/deploy/freeframe"
  assert_eq "$(cat "$FF_ROOT/x")" "downloaded"
}

@test "fetch that fails leaves the old file in place" {
  printf 'old\n' >"$FF_ROOT/x"
  curl() { return 22; }
  run fetch latest docker-compose.prod.yml "$FF_ROOT/x"
  [ "$status" -ne 0 ]
  assert_eq "$(cat "$FF_ROOT/x")" "old"
  [ -z "$(ls "$FF_ROOT" | grep -v '^x$')" ]
}

@test "port_holder names the program and its pid" {
  ss() { printf '%s\n' 'LISTEN 0 511 0.0.0.0:8080 0.0.0.0:* users:(("other",pid=1,fd=3))' \
    'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=812,fd=6))'; }
  assert_eq "$(port_holder 80)" "nginx 812"
  assert_eq "$(port_holder 443)" ""
}

@test "port_holder matches IPv6 and wildcard listeners but not a longer port" {
  ss() { printf '%s\n' 'LISTEN 0 511 [::]:4430 [::]:* users:(("a",pid=1,fd=3))' \
    'LISTEN 0 511 *:443 *:* users:(("caddy",pid=77,fd=3))'; }
  assert_eq "$(port_holder 443)" "caddy 77"
}

@test "docker_status reads the server version and the compose plugin" {
  docker() {
    case "$1" in
      version) echo "${V:-27.3.1}" ;;
      compose) [[ -z ${NOCOMPOSE:-} ]] ;;
    esac
  }
  assert_eq "$(docker_status)" "ok"
  V=23.0.6
  assert_eq "$(docker_status)" "old 23.0.6"
  V=24.0.0 NOCOMPOSE=1
  assert_eq "$(docker_status)" "nocompose"
}

@test "docker_status says none without docker" {
  PATH="$BATS_TEST_TMPDIR/empty" run docker_status
  assert_eq "$output" "none"
}

@test "switch_to_http_mode changes only the address keys and restarts" {
  FF_IP=203.0.113.7 FF_HTTPS=sslip FF_ACME_EMAIL=c@example.com derive_env "$ENV"
  cp "$ENV" "$BATS_TEST_TMPDIR/before"
  dc() { printf 'dc %s\n' "$*" >>"$CALLS"; }
  probe_health() { :; }
  run switch_to_http_mode "$ENV" 203.0.113.7
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  grep -q -- "dc up -d --no-build --force-recreate" "$CALLS"
  # Exactly these keys changed; everything else is byte for byte the same.
  diff <(grep -vE '^(FREEFRAME_HTTPS|FRONTEND_URL|DOMAIN|S3_PUBLIC_ENDPOINT|STORAGE_DOMAIN|STORAGE_ROUTED|STORAGE_PORT_BIND)=' "$BATS_TEST_TMPDIR/before") \
    <(grep -vE '^(FREEFRAME_HTTPS|FRONTEND_URL|DOMAIN|S3_PUBLIC_ENDPOINT|STORAGE_DOMAIN|STORAGE_ROUTED|STORAGE_PORT_BIND)=' "$ENV")
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
  assert_has "$ENV" "FRONTEND_URL=http://203.0.113.7"
  assert_has "$ENV" "DOMAIN="
  assert_has "$ENV" "STORAGE_DOMAIN="
  assert_has "$ENV" "ACME_EMAIL=c@example.com"
  assert_eq "$(compose_files "$ENV")" "-f docker-compose.prod.yml -f docker-compose.images.yml -f docker-compose.storage.yml"
  [ "$(ls "$ENV".2* | wc -l)" -eq 1 ]
}

@test "_ff_json_str escapes every control character as \\u00XX" {
  local in
  in=$(printf 'a\tb\001c\037d"e\\f')
  in+=$'\n'"g"$'\r'
  assert_eq "$(_ff_json_str "$in")" '"a\u0009b\u0001c\u001fd\"e\\f\u000ag\u000d"'
  assert_eq "$(_ff_json_str 'plain')" '"plain"'
}

@test "ufw_active reads ufw in the C locale" {
  ufw() {
    if [[ ${LC_ALL:-} == C ]]; then echo "Status: active"; else echo "Statut : actif"; fi
  }
  LC_ALL=fr_FR.UTF-8 ufw_active
}

@test "help steers passwords to FREEFRAME_ variables and warns that flags show in ps" {
  run bash "$FF_SCRIPT" help
  [ "$status" -eq 0 ]
  assert_out "FREEFRAME_ADMIN_PASSWORD"
  assert_out "ps"
}

# ---------------------------------------------------------------- install.sh

bootstrap_setup() {
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$BIN" "$BATS_TEST_TMPDIR/tmp"
  # Where mktemp puts the download, so a test can see it is cleaned up.
  export TMPDIR="$BATS_TEST_TMPDIR/tmp"
  # The script curl "downloads": it reports how it was run.
  export DOWNLOAD="$BATS_TEST_TMPDIR/download"
  printf '%s\n' '#!/usr/bin/env bash' 'echo "ran: $*"' 'exit "${FAKE_RC:-0}"' >"$DOWNLOAD"
  cat >"$BIN/curl" <<'EOF'
#!/bin/sh
out=""
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift ;;
    https://*) echo "$1" >>"$CALLS" ;;
  esac
  shift
done
[ -n "${CURL_FAIL:-}" ] && exit 22
cp "$DOWNLOAD" "$out"
EOF
  # A Docker with nothing on it, for runs of the real script.
  printf '%s\n' '#!/bin/sh' 'exit 0' >"$BIN/docker"
  # Root, as under sudo, whoever runs the suite (CI runs it as a normal
  # user). The not-root test replaces this with a normal user's id.
  printf '%s\n' '#!/bin/sh' "if [ \"\$1\" = -u ]; then echo 0; else exec $(command -v id) \"\$@\"; fi" >"$BIN/id"
  chmod +x "$BIN/curl" "$BIN/docker" "$BIN/id"
}

run_bootstrap() {
  run env PATH="$BIN:$PATH" sh "${BATS_TEST_DIRNAME}/../../install.sh" "$@"
}

@test "install.sh: runs the downloaded install on the latest channel and writes nothing" {
  bootstrap_setup
  run_bootstrap --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(cat "$CALLS")" \
    "https://raw.githubusercontent.com/Techiebutler/freeframe/latest/deploy/freeframe"
  assert_out "ran: install --channel latest --yes"
  [ ! -e "$FF_BIN_DIR/freeframe" ]
  # The temporary download is gone afterwards.
  [ -z "$(ls -A "$TMPDIR")" ]
}

@test "install.sh: passes on the exit code of freeframe install" {
  bootstrap_setup
  export FAKE_RC=3
  run_bootstrap --yes
  [ "$status" -eq 3 ]
  [ -z "$(ls -A "$TMPDIR")" ]
}

@test "install.sh: FREEFRAME_CHANNEL picks the ref" {
  bootstrap_setup
  export FREEFRAME_CHANNEL=stable
  run_bootstrap
  [ "$status" -eq 0 ]
  grep -q '/stable/deploy/freeframe$' "$CALLS"
  assert_out "ran: install --channel stable"
}

@test "install.sh: --channel picks the ref the script is downloaded from" {
  bootstrap_setup
  export FREEFRAME_CHANNEL=stable
  run_bootstrap --yes --channel v1.22.0
  [ "$status" -eq 0 ]
  grep -q '/v1.22.0/deploy/freeframe$' "$CALLS"
  assert_out "ran: install --channel v1.22.0 --yes --channel v1.22.0"
  : >"$CALLS"
  run_bootstrap --channel=latest
  [ "$status" -eq 0 ]
  grep -q '/latest/deploy/freeframe$' "$CALLS"
}

@test "install.sh: a download that is not the script is refused" {
  bootstrap_setup
  printf '%s\n' '<html>404</html>' >"$DOWNLOAD"
  run_bootstrap
  [ "$status" -eq 1 ]
  assert_out "What to try"
  # Already on latest: no circular "try latest".
  if [[ $output == *"FREEFRAME_CHANNEL=latest"* ]]; then echo "$output"; false; fi
  assert_out "a release after v1.21.0"
  [ -z "$(ls -A "$TMPDIR")" ]
  FREEFRAME_CHANNEL=stable run_bootstrap
  [ "$status" -eq 1 ]
  assert_out "sudo FREEFRAME_CHANNEL=latest bash"
}

@test "install.sh: a cut-off download (bash -n fails) is refused and not run" {
  bootstrap_setup
  printf '%s\n' '#!/usr/bin/env bash' 'echo "ran: $*"' 'main() {' '  if true; then' >"$DOWNLOAD"
  run_bootstrap
  [ "$status" -eq 1 ]
  if grep -q "ran:" <<<"$output"; then echo "$output"; false; fi
  assert_out "not a complete freeframe command"
}

@test "install.sh: a failed download says so" {
  bootstrap_setup
  export CURL_FAIL=1
  run_bootstrap
  [ "$status" -eq 1 ]
  assert_out "Could not download the freeframe command"
  if [[ $output == *"FREEFRAME_CHANNEL=latest"* ]]; then echo "$output"; false; fi
  assert_out "a release after v1.21.0"
  FREEFRAME_CHANNEL=stable run_bootstrap
  [ "$status" -eq 1 ]
  assert_out "sudo FREEFRAME_CHANNEL=latest bash"
}

@test "install.sh: a bad channel name exits 2" {
  bootstrap_setup
  export FREEFRAME_CHANNEL='x;rm'
  run_bootstrap
  [ "$status" -eq 2 ]
  [ ! -s "$CALLS" ]
}

@test "install.sh: not root exits 2 and shows the sudo form that keeps the channel" {
  bootstrap_setup
  printf '%s\n' '#!/bin/sh' 'echo 1000' >"$BIN/id"
  chmod +x "$BIN/id"
  run_bootstrap
  [ "$status" -eq 2 ]
  assert_out "| sudo FREEFRAME_CHANNEL=latest bash"
  [ ! -s "$CALLS" ]
}

@test "install.sh with the real script: a refused run leaves no freeframe command" {
  bootstrap_setup
  cp "$FF_SCRIPT" "$DOWNLOAD"
  os_release debian 11 bullseye
  run_bootstrap --yes
  [ "$status" -eq 3 ] || { echo "$output"; false; }
  assert_out "not supported yet"
  [ ! -e "$FF_BIN_DIR/freeframe" ]
  assert_root_untouched
}

@test "install.sh with the real script: an installed server keeps its freeframe command" {
  bootstrap_setup
  cp "$FF_SCRIPT" "$DOWNLOAD"
  mkdir -p "$FF_BIN_DIR"
  printf '%s\n' '#!/usr/bin/env bash' '# the installed copy' >"$FF_BIN_DIR/freeframe"
  cp "$FF_BIN_DIR/freeframe" "$BATS_TEST_TMPDIR/bin.before"
  : >"$ENV"
  : >"$FF_ROOT/.installed"
  run_bootstrap --yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "already installed"
  cmp "$FF_BIN_DIR/freeframe" "$BATS_TEST_TMPDIR/bin.before"
}

# ---------------------------------------------------------------- hints and state lines

@test "install: a program is listed as stopped only when stopping it worked" {
  printf '%s\n' "y" "" "certs@example.com" "1" "" "Grace" "grace@example.com" \
    "long-password-1" "long-password-1" "y" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  export STUB_SS='LISTEN 0 511 [::]:443 [::]:* users:(("apache2",pid=900,fd=4))'
  export STUB_UNIT=apache2.service STUB_SYSTEMCTL_FAIL=1
  ff_install
  [ "$status" -eq 1 ]
  assert_out "Could not stop apache2."
  if [[ $output == *"Already done: stopped apache2"* ]]; then echo "$output"; false; fi
}

@test "install: before the command is installed, a bad option points at the one-line install command" {
  # No freeframe command in FF_BIN_DIR: the person ran the bootstrap.
  run bash -c 'source "$1"; main install --channel stable --bogus' ff "$FF_SCRIPT"
  [ "$status" -eq 2 ]
  assert_out "Unknown option: --bogus"
  assert_out "curl -fsSL https://raw.githubusercontent.com/Techiebutler/freeframe/stable/install.sh | sudo bash -s -- --help"
  if [[ $output == *"run 'freeframe help'"* ]]; then echo "$output"; false; fi
  # Without a channel: latest, like install.sh.
  run bash -c 'source "$1"; main install --bogus' ff "$FF_SCRIPT"
  assert_out "/freeframe/latest/install.sh | sudo bash -s -- --help"
  # Once the command is installed, it is named.
  mkdir -p "$FF_BIN_DIR"
  printf '#!/usr/bin/env bash\n' >"$FF_BIN_DIR/freeframe"
  chmod 755 "$FF_BIN_DIR/freeframe"
  run bash -c 'source "$1"; main install --bogus' ff "$FF_SCRIPT"
  [ "$status" -eq 2 ]
  assert_out "run 'freeframe help'"
  if [[ $output == *"install.sh"* ]]; then echo "$output"; false; fi
}

@test "install: with no terminal before the command is installed, the hint is the one-line install command" {
  plain_answers
  ff_install --channel stable
  [ "$status" -eq 2 ]
  assert_out "curl -fsSL https://raw.githubusercontent.com/Techiebutler/freeframe/stable/install.sh | sudo bash -s -- --yes --admin-email you@yourstudio.com"
  if [[ $output == *" freeframe install --yes"* ]]; then echo "$output"; false; fi
  mkdir -p "$FF_BIN_DIR"
  printf '#!/usr/bin/env bash\n' >"$FF_BIN_DIR/freeframe"
  chmod 755 "$FF_BIN_DIR/freeframe"
  ff_install --channel stable
  [ "$status" -eq 2 ]
  assert_out "sudo freeframe install --yes --admin-email you@yourstudio.com"
}

@test "install: answers ending before the command is installed point at the one-line install command" {
  printf '%s\n' "" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
  ff_install --channel stable
  [ "$status" -eq 2 ]
  assert_out "The answers ended before every question was answered"
  assert_out "/freeframe/stable/install.sh | sudo bash -s -- --help"
  if [[ $output == *"see: freeframe help"* ]]; then echo "$output"; false; fi
}

@test "install: a failure after server changes lists what was already done" {
  plain_answers
  export FF_RAM_MB=3000 STUB_UFW_ACTIVE=1 STUB_FETCH_FAIL=docker-compose.https.yml
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "Could not download docker-compose.https.yml"
  assert_out "installed the freeframe command at $FF_BIN_DIR/freeframe"
  assert_out "installed Docker"
  assert_out "added a 2 GB swap file"
  assert_out "ufw firewall"
  if [[ $output == *"has only written its log"* ]]; then echo "$output"; false; fi
  # A failed download replaces none of the setup files, so it is not claimed.
  if [[ $output == *"downloaded the setup files"* ]]; then echo "$output"; false; fi
}

@test "install: a failure right after the download says the setup files are there" {
  plain_answers
  export STUB_DOCKER=ok STUB_DERIVE_FAIL=1
  ff_install --yes
  [ "$status" -eq 1 ]
  assert_out "downloaded the setup files into $FF_ROOT"
  if [[ $output == *"installed Docker"* ]]; then echo "$output"; false; fi
}

@test "install: the one-line install command in a hint follows the settings file's channel" {
  printf 'FREEFRAME_CHANNEL=stable\n' >"$ENV"
  run bash -c 'source "$1"; main install --bogus' ff "$FF_SCRIPT"
  [ "$status" -eq 2 ]
  assert_out "/freeframe/stable/install.sh | sudo bash -s -- --help"
  # An explicit --channel still wins.
  run bash -c 'source "$1"; main install --channel v1.22.0 --bogus' ff "$FF_SCRIPT"
  assert_out "/freeframe/v1.22.0/install.sh | sudo bash -s -- --help"
}
