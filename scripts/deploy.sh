#!/usr/bin/env bash
# Builds and releases the application on this server, with a pre-deploy database backup, validation and
# automatic rollback to the previous release if validation fails.
#   ./scripts/deploy.sh              deploy the code in this directory (uploaded archive or current checkout)
#   ./scripts/deploy.sh <git-ref>    fetch from origin, check out the ref (branch, tag or commit), deploy it
# Environment: SKIP_BACKUP=1 (no pre-deploy backup), KEEP_RELEASES=5, WAIT_TIMEOUT=900
set -Eeuo pipefail
. "$(dirname "$0")/lib.sh"

main() {
  local ref=${1:-} version previous started
  need_env
  mkdir -p .deploy/releases
  exec 9>.deploy/lock
  flock -n 9 || die "Another deployment is running (.deploy/lock)."
  started=$(date +%s)

  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is not installed."
  local free_gb
  free_gb=$(df --output=avail -BG "$ROOT" | tail -n1 | tr -dc '0-9')
  [ "${free_gb:-0}" -ge 5 ] || die "Less than 5 GB free disk space (${free_gb} GB). Free space first: docker image prune -a"

  if [ -n "$ref" ]; then
    git rev-parse --git-dir >/dev/null 2>&1 || die "A git ref was given but $ROOT is not a git checkout."
    log "Fetching $ref"
    git fetch --tags --prune origin
    git checkout --quiet --detach "$(git rev-parse --verify --quiet "origin/$ref" || git rev-parse --verify "$ref")"
  fi
  if git rev-parse --git-dir >/dev/null 2>&1; then
    version=$(git rev-parse --short=12 HEAD)
    git diff --quiet HEAD -- . ':!.env' 2>/dev/null || version="$version-$(date -u +%Y%m%d%H%M%S)"
  else
    version="r$(date -u +%Y%m%d%H%M%S)"
  fi
  previous=$(env_get APP_VERSION)
  log "Deploying version $version (previous: ${previous:-none})"

  ./scripts/upgrade-env.sh
  APP_VERSION=$version docker compose config -q || die "The compose configuration is invalid."

  log "Building images"
  APP_VERSION=$version docker compose build backend frontend

  if [ "${SKIP_BACKUP:-0}" != "1" ] && [ -n "$(docker compose ps -q db 2>/dev/null)" ]; then
    log "Backing up the database before the release"
    ./scripts/backup.sh --db-only --label "pre-$version"
  fi

  if has_proxy; then
    ./scripts/ssl-init.sh --placeholder
  fi

  env_set APP_VERSION "$version"
  log "Starting the release (migrations run first)"
  if ! docker compose up -d --wait --wait-timeout "${WAIT_TIMEOUT:-900}" --remove-orphans; then
    failed "$version" "$previous" "the stack did not become healthy"
  fi

  if has_proxy; then
    ./scripts/ssl-init.sh || warn "No trusted certificate yet. Fix DNS/port 80 and run ./scripts/ssl-init.sh"
  fi

  log "Validating"
  if ! ./scripts/validate.sh; then
    failed "$version" "$previous" "post-deployment validation failed"
  fi

  local revision
  revision=$(alembic_current)
  tar czf ".deploy/releases/$version.tar.gz" --exclude='infra/**/__pycache__' \
    docker-compose.yml docker-compose.prod.yml infra scripts Makefile .env.example
  printf '%s %s %s deploy\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$version" "${revision:-unknown}" >> .deploy/history
  prune_releases
  log "Deployed $version (database revision ${revision:-unknown}) in $(( $(date +%s) - started )) s"
}

failed() {  # version previous reason
  local version=$1 previous=$2 reason=$3
  log "DEPLOYMENT FAILED: $reason"
  docker compose ps || true
  docker compose logs --no-color --tail=60 migrate backend frontend 2>/dev/null | tail -n 150 || true
  if [ -n "$previous" ] && [ "$previous" != "$version" ] && docker image inspect "copilot-backend:$previous" >/dev/null 2>&1; then
    log "Rolling back to $previous"
    ./scripts/rollback.sh "$previous" --keep-db --auto || die "Automatic rollback failed. See docs/DEPLOYMENT.md, section 15."
    die "Version $version was not released; $previous is running again."
  fi
  die "Version $version is not healthy and there is no previous release to roll back to."
}

prune_releases() {
  local keep=${KEEP_RELEASES:-5} current old
  current=$(env_get APP_VERSION)
  awk '{print $2}' .deploy/history | awk '!seen[$0]++' | tac | tail -n "+$((keep + 1))" | while read -r old; do
    [ "$old" = "$current" ] && continue
    docker image rm "copilot-backend:$old" "copilot-frontend:$old" >/dev/null 2>&1 || true
    rm -f ".deploy/releases/$old.tar.gz"
  done
  docker image prune -f >/dev/null 2>&1 || true
}

main "$@"
