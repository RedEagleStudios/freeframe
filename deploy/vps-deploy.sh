#!/usr/bin/env bash
# Deploy the current origin/<branch> to this server: pull, build, restart, check health.
#
# Installed as /usr/local/sbin/freeframe-deploy and run by the CI deploy key as a
# forced command (see .github/workflows/deploy.yml), so that key can do this and
# nothing else. Also fine to run by hand. Exits non-zero when the new version
# does not come up healthy, which fails the workflow run.
#
# Images are built before anything is restarted, so a failed build leaves the
# running version untouched.
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/freeframe}
BRANCH=${DEPLOY_BRANCH:-re-main}
COMPOSE_FILES=${COMPOSE_FILES:-"-f docker-compose.prod.yml -f docker-compose.cloudflare.yml"}
HEALTH_URL=${HEALTH_URL:-}   # e.g. https://review.example.com/api/health; empty = skip
LOG=/var/log/freeframe-deploy.log

exec > >(tee -a "$LOG") 2>&1
# One deploy at a time: a second push waits for the first instead of racing it.
exec 9>/run/freeframe-deploy.lock
flock 9

echo "=== $(date -u +%FT%TZ) deploy $BRANCH"
cd "$APP_DIR"
git fetch --quiet origin "$BRANCH"
git reset --quiet --hard "origin/$BRANCH"
echo "at $(git log --oneline -1)"

# shellcheck disable=SC2086
C="docker compose --env-file .env.prod $COMPOSE_FILES"
$C build --quiet
$C up -d --remove-orphans

# Wait for the API and web containers' own health checks.
for i in $(seq 1 40); do
  unhealthy=$($C ps --format '{{.Service}} {{.Health}}' | awk '$1 ~ /^(api|web)$/ && $2 != "healthy"' | wc -l)
  [ "$unhealthy" -eq 0 ] && break
  sleep 5
done
$C ps --format '{{.Service}}\t{{.Status}}'
[ "$unhealthy" -eq 0 ] || { echo "api/web not healthy after deploy" >&2; exit 1; }

if [ -n "$HEALTH_URL" ]; then
  curl -fsS --max-time 15 -A "freeframe-deploy" "$HEALTH_URL" >/dev/null || { echo "health check failed: $HEALTH_URL" >&2; exit 1; }
fi

docker image prune -f >/dev/null
echo "=== deployed $(git rev-parse --short HEAD)"
