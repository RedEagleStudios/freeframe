#!/usr/bin/env bats
# `freeframe configure` (spec goal 12c), with every side effect stubbed
# (manage_stubs.bash). Each flow runs in a fresh bash under the script's own
# strict mode.

load manage_helpers

setup() {
  manage_setup
}

# tty LINE...: the answers typed at the terminal, one per line.
tty() {
  printf '%s\n' "$@" >"$BATS_TEST_TMPDIR/tty"
  export FF_TTY="$BATS_TEST_TMPDIR/tty"
}

keep_copy() { cp "$ENV" "$BATS_TEST_TMPDIR/env.before"; }
BEFORE() { printf '%s' "$BATS_TEST_TMPDIR/env.before"; }

# ---------------------------------------------------------------- address

@test "configure: a new own domain rewrites only the address keys, after a backup, and keeps every secret" {
  installed_server sslip bundled
  keep_copy
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(changed_keys "$(BEFORE)" "$ENV")" \
    "DOMAIN FREEFRAME_HTTPS FRONTEND_URL S3_PUBLIC_ENDPOINT STORAGE_DOMAIN"
  assert_has "$ENV" "FRONTEND_URL=https://review.yourstudio.com"
  assert_has "$ENV" "DOMAIN=review.yourstudio.com"
  assert_has "$ENV" "STORAGE_DOMAIN=storage.review.yourstudio.com"
  assert_has "$ENV" "S3_PUBLIC_ENDPOINT=https://storage.review.yourstudio.com"
  assert_has "$ENV" "FREEFRAME_HTTPS=own"
  assert_secrets_same "$(BEFORE)" "$ENV"
  # The backup holds the settings exactly as they were.
  local b
  b=$(env_backups)
  [ "$(wc -w <<<"$b")" -eq 1 ]
  cmp "$FF_ROOT/$b" "$(BEFORE)"
  [ "$(stat -c %a "$FF_ROOT/$b")" = 600 ]
  assert_out "$FF_ROOT/$b"
  assert_order "dns_points_here review.yourstudio.com 203.0.113.7" \
    "dns_points_here storage.review.yourstudio.com 203.0.113.7" \
    "dc up -d --no-build --force-recreate" "localhost:8000/health" \
    "probe_health own review.yourstudio.com" "cert_issued review.yourstudio.com"
}

@test "configure: a new address warns that links already sent by email stop working" {
  installed_server sslip bundled
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "Links already sent by email"
  assert_out "https://203-0-113-7.sslip.io"
  assert_out "stop working"
}

@test "configure: a new own domain waits for its DNS records before changing anything" {
  installed_server sslip bundled
  keep_copy
  export STUB_DNS_FAILS=1000 FF_DNS_WAIT=30
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 1 ]
  assert_out "do not point at this server"
  assert_out "sudo freeframe configure"
  cmp "$ENV" "$(BEFORE)"
  [ -z "$(env_backups)" ]
  assert_not_called "dc up"
  grep -q "sleep 15" "$CALLS"
}

@test "configure: DNS that starts pointing here after a while is waited for" {
  installed_server sslip bundled
  export STUB_DNS_FAILS=2
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_order "dns_points_here review.yourstudio.com" "sleep 15" "dns_points_here review.yourstudio.com" \
    "dc up -d --no-build --force-recreate"
}

@test "configure: moving to plain HTTP uses the server's address and keeps every secret" {
  installed_server sslip bundled
  keep_copy
  ff configure --yes --https off
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=off"
  assert_has "$ENV" "FRONTEND_URL=http://203.0.113.7"
  assert_has "$ENV" "S3_PUBLIC_ENDPOINT=http://203.0.113.7:9000"
  assert_has "$ENV" "STORAGE_ROUTED=false"
  assert_has "$ENV" "STORAGE_PORT_BIND=0.0.0.0:9000"
  assert_eq "$(env_get "$ENV" DOMAIN)" ""
  assert_secrets_same "$(BEFORE)" "$ENV"
  assert_has "$ENV" "ACME_EMAIL=certs@example.com"
  grep -q "dc up -d --no-build --force-recreate" "$CALLS"
  assert_out "http://203.0.113.7"
}

@test "configure: behind a router, plain HTTP uses the server's own network address" {
  installed_server sslip bundled
  export STUB_LOCAL_IP=10.0.0.5
  ff configure --yes --https off
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FRONTEND_URL=http://10.0.0.5"
}

@test "configure: turning HTTPS on for a plain HTTP server asks for the certificate email" {
  installed_server off bundled
  sed -i '/^ACME_EMAIL=/d' "$ENV"
  keep_copy
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 2 ]
  assert_out "FREEFRAME_ACME_EMAIL"
  cmp "$ENV" "$(BEFORE)"

  ff configure --yes --domain review.yourstudio.com --acme-email certs@yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "FREEFRAME_HTTPS=own"
  assert_has "$ENV" "FRONTEND_URL=https://review.yourstudio.com"
  assert_has "$ENV" "ACME_EMAIL=certs@yourstudio.com"
  assert_has "$ENV" "STORAGE_ROUTED=true"
  assert_has "$ENV" "STORAGE_PORT_BIND=127.0.0.1:9000"
  assert_secrets_same "$(BEFORE)" "$ENV"
}

@test "configure: a certificate that does not arrive is reported, not hidden" {
  installed_server sslip bundled
  export STUB_CERT_FAIL=1 FF_CERT_WAIT=20
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "has not arrived yet"
  assert_out "sudo freeframe restart"
}

@test "configure: FreeFrame not answering afterwards says how to put the old settings back" {
  installed_server sslip bundled
  export STUB_API_DOWN=1 FF_API_WAIT=10
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 1 ]
  local b
  b=$(env_backups)
  assert_out "sudo cp '$FF_ROOT/$b' '$ENV' && sudo freeframe restart"
}

# ---------------------------------------------------------------- email

@test "configure: adding SMTP changes only SMTP_* and MAIL_FROM_ADDRESS" {
  installed_server sslip bundled
  keep_copy
  export FREEFRAME_SMTP_PASSWORD='p@ss $word'
  ff configure --yes --smtp-host smtp.example.com --smtp-user mailer@example.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  local k
  for k in $(changed_keys "$(BEFORE)" "$ENV"); do
    [[ $k == SMTP_* || $k == MAIL_FROM_ADDRESS ]] || { echo "unexpected change: $k"; false; }
  done
  assert_has "$ENV" "SMTP_HOST=smtp.example.com"
  assert_has "$ENV" "SMTP_PORT=587"
  assert_has "$ENV" "SMTP_USE_TLS=true"
  assert_has "$ENV" "SMTP_USER=mailer@example.com"
  assert_eq "$(env_get "$ENV" SMTP_PASSWORD)" 'p@ss $word'
  assert_has "$ENV" "MAIL_FROM_ADDRESS=mailer@example.com"
  assert_secrets_same "$(BEFORE)" "$ENV"
  cmp "$FF_ROOT/$(env_backups)" "$(BEFORE)"
  # No address change: no warning about links, no DNS check.
  assert_no_out "Links already sent"
  assert_not_called dns_points_here
  # The password is never printed.
  assert_no_out 'p@ss $word'
}

@test "configure: port 465 means TLS from the first byte" {
  installed_server sslip bundled
  ff configure --yes --smtp-host smtp.example.com --smtp-port 465 --smtp-from noreply@example.com
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "SMTP_USE_TLS=false"
  assert_has "$ENV" "MAIL_FROM_ADDRESS=noreply@example.com"
}

@test "configure: SMTP without a from address is refused before anything changes" {
  installed_server sslip bundled
  keep_copy
  ff configure --yes --smtp-host smtp.example.com
  [ "$status" -eq 2 ]
  assert_out "FREEFRAME_SMTP_FROM"
  cmp "$ENV" "$(BEFORE)"
}

# ---------------------------------------------------------------- storage

@test "configure: switching where footage is kept is refused with a plain explanation" {
  installed_server sslip bundled
  keep_copy
  ff configure --yes --storage s3 --s3-bucket media
  [ "$status" -eq 2 ]
  assert_out "does not move footage"
  cmp "$ENV" "$(BEFORE)"
  assert_not_called "dc "

  # The bundled storage's keys are generated secrets: never set from outside.
  export FREEFRAME_S3_SECRET_KEY=mine
  ff configure --yes
  [ "$status" -eq 2 ]
  cmp "$ENV" "$(BEFORE)"
}

@test "configure: the keys of your own S3 bucket can be changed" {
  installed_server sslip s3
  keep_copy
  export FREEFRAME_S3_SECRET_KEY=new-secret
  ff configure --yes --s3-access-key AKNEW
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(changed_keys "$(BEFORE)" "$ENV")" "S3_ACCESS_KEY S3_SECRET_KEY"
  assert_has "$ENV" "S3_ACCESS_KEY=AKNEW"
  assert_has "$ENV" "S3_SECRET_KEY=new-secret"
  assert_no_out new-secret
}

@test "configure: a new bucket says that footage already uploaded stays in the old one" {
  installed_server sslip s3
  ff configure --yes --s3-bucket media2
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "S3_BUCKET=media2"
  assert_out "stays in media"
}

# ---------------------------------------------------------------- interactive

@test "configure: Enter at every question changes nothing" {
  installed_server sslip bundled
  keep_copy
  tty "" "" "" ""
  ff configure
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "Nothing to change"
  cmp "$ENV" "$(BEFORE)"
  [ -z "$(env_backups)" ]
  assert_not_called "dc "
}

@test "configure: interactive, a typed domain is shown with the warning and saved after a yes" {
  installed_server sslip bundled
  keep_copy
  # address, email (Enter keeps none), storage (Enter keeps), confirm.
  tty "review.yourstudio.com" "" "" "y"
  ff configure
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "Now: https://203-0-113-7.sslip.io"
  assert_out "Links already sent by email"
  assert_has "$ENV" "FRONTEND_URL=https://review.yourstudio.com"
  assert_secrets_same "$(BEFORE)" "$ENV"
}

@test "configure: interactive, answering no changes nothing" {
  installed_server sslip bundled
  keep_copy
  tty "review.yourstudio.com" "" "" "n"
  ff configure
  [ "$status" -eq 1 ]
  assert_out "Nothing was changed"
  cmp "$ENV" "$(BEFORE)"
  assert_not_called "dc "
}

@test "configure: interactive, choosing the other storage explains and keeps footage where it is" {
  installed_server sslip bundled
  keep_copy
  tty "" "" "2" ""
  ff configure
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "does not move footage"
  cmp "$ENV" "$(BEFORE)"
}

@test "configure: interactive SMTP, the password is typed and kept out of the output" {
  installed_server sslip bundled
  tty "" "smtp.example.com" "587" "mailer@example.com" "s3cret-mail" "" "" "y"
  ff configure
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_has "$ENV" "SMTP_HOST=smtp.example.com"
  assert_has "$ENV" "SMTP_PASSWORD=s3cret-mail"
  assert_has "$ENV" "MAIL_FROM_ADDRESS=mailer@example.com"
  assert_no_out "s3cret-mail"
}

# ---------------------------------------------------------------- refusals and channel

@test "configure: with no terminal and no --yes it stops and names --yes" {
  installed_server sslip bundled
  keep_copy
  ff configure --domain review.yourstudio.com
  [ "$status" -eq 2 ]
  assert_out "--yes"
  cmp "$ENV" "$(BEFORE)"
}

@test "configure: --yes with nothing to change says so" {
  installed_server sslip bundled
  ff configure --yes
  [ "$status" -eq 0 ]
  assert_out "Nothing to change"
  assert_not_called "dc "
}

@test "configure: --channel is saved after a backup and needs no restart" {
  installed_server sslip bundled
  keep_copy
  ff configure --yes --channel stable
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_eq "$(changed_keys "$(BEFORE)" "$ENV")" "FREEFRAME_CHANNEL"
  cmp "$FF_ROOT/$(env_backups)" "$(BEFORE)"
  assert_not_called "dc up"
  assert_out "sudo freeframe update"
}

@test "configure: refuses before the install finished and while a restore is unfinished" {
  installed_server sslip bundled
  rm "$FF_ROOT/.installed"
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 1 ]
  assert_out "sudo freeframe install"
  printf 'FreeFrame installed\n' >"$FF_ROOT/.installed"
  mkdir -p "$BK"
  printf 'PGDMP old\n' >"$BK/freeframe-20261020-030000.dump"
  printf '%s\n' "$BK/freeframe-20261020-030000.dump" >"$FF_ROOT/.restore-undo"
  ff configure --yes --domain review.yourstudio.com
  [ "$status" -eq 1 ]
  assert_out "A restore of the database did not finish"
  assert_not_called "dc "
}

@test "configure: answers that end part-way change nothing and say so" {
  installed_server sslip bundled
  keep_copy
  tty "review.yourstudio.com"
  ff configure
  [ "$status" -eq 2 ]
  assert_out "so nothing was changed"
  assert_out "sudo freeframe configure in a terminal"
  cmp "$ENV" "$(BEFORE)"
}

@test "configure: at the DNS wait, s goes back to the temporary address it already had: nothing to change" {
  installed_server sslip bundled
  keep_copy
  export STUB_DNS_FAILS=1000
  tty "review.yourstudio.com" "" "" "y" "s"
  ff configure
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  assert_out "Nothing to change"
  cmp "$ENV" "$(BEFORE)"
  [ -z "$(env_backups)" ]
  assert_not_called "dc up"
}
