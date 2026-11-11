#!/usr/bin/env bash
# Switches back to a previous release. Images of the last releases stay on the server, so nothing is rebuilt.
#   ./scripts/rollback.sh                       previous release, keeping the database as it is
#   ./scripts/rollback.sh <version> --keep-db   that release, database untouched (schema changes must be additive)
#   ./scripts/rollback.sh <version> --with-db   also downgrade the schema to that release's revision. Data in
#                                               tables and columns added since then is LOST. A backup is taken first.
#   ./scripts/rollback.sh --list                show releases
# To go back to the data as it was before a release instead, restore its pre-deploy backup: ./scripts/restore.sh
set -Eeuo pipefail
. "$(dirname "$0")/lib.sh"

main() {
  local target="" db_mode="" auto=0 arg current
  need_env
  [ -f .deploy/history ] || die "No release history (.deploy/history)."
  for arg in "$@"; do
    case "$arg" in
      --list) list; exit 0 ;;
      --keep-db) db_mode=keep ;;
      --with-db) db_mode=downgrade ;;
      --auto) auto=1 ;;
      -*) die "Unknown option $arg" ;;
      *) target=$arg ;;
    esac
  done
  current=$(env_get APP_VERSION)
  if [ -z "$target" ]; then
    target=$(awk '$4 == "deploy" {print $2}' .deploy/history | awk -v cur="$current" '$0 != cur' | tail -n1)
    [ -n "$target" ] || die "There is no earlier release to roll back to."
  fi
  [ "$target" != "$current" ] || [ "$auto" = 1 ] || die "$target is already the running version."
  docker image inspect "copilot-backend:$target" "copilot-frontend:$target" >/dev/null 2>&1 \
    || die "Images for $target are no longer on this server. Deploy that version instead: ./scripts/deploy.sh <git-ref>"

  local target_rev current_rev
  target_rev=$(awk -v v="$target" '$2 == v && $4 == "deploy" {rev = $3} END {print rev}' .deploy/history)
  current_rev=$(alembic_current)
  log "Rolling back from ${current:-unknown} (revision ${current_rev:-unknown}) to $target (revision ${target_rev:-unknown})"

  if [ -n "$target_rev" ] && [ -n "$current_rev" ] && [ "$target_rev" != "$current_rev" ] && [ -z "$db_mode" ]; then
    die "The database is at revision $current_rev but $target expects $target_rev. Choose --keep-db (schema stays; fine when the newer migrations only added things) or --with-db (downgrade the schema; data added by the newer migrations is lost)."
  fi

  if [ "$db_mode" = "downgrade" ] && [ -n "$target_rev" ] && [ "$target_rev" != "$current_rev" ]; then
    log "Backing up the database before the schema downgrade"
    ./scripts/backup.sh --db-only --label "pre-rollback-$target"
    log "Stopping the application and downgrading the schema to $target_rev (with the current release's migrations)"
    docker compose stop frontend backend worker
    docker compose run --rm --no-deps -T migrate alembic downgrade "$target_rev"
  fi

  # Bring back the deployment files of that release (compose files, proxy and monitoring configuration, scripts).
  if git rev-parse --git-dir >/dev/null 2>&1 && git cat-file -e "$target^{commit}" 2>/dev/null; then
    git checkout --quiet --detach "$target"
  elif [ -f ".deploy/releases/$target.tar.gz" ]; then
    tar xzf ".deploy/releases/$target.tar.gz"
  else
    warn "No snapshot of $target's deployment files; keeping the current ones."
  fi

  env_set APP_VERSION "$target"
  # The migration container is skipped on purpose: the older code must not try to migrate a newer schema.
  # shellcheck disable=SC2046
  docker compose up -d --no-build --no-deps --wait --wait-timeout "${WAIT_TIMEOUT:-600}" $(long_running_services)

  printf '%s %s %s rollback\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$target" "$(alembic_current || true)" >> .deploy/history
  if ./scripts/validate.sh; then
    log "Rolled back to $target"
  else
    die "Rolled back to $target, but validation reports problems. Check: docker compose ps; docker compose logs --tail=100 backend"
  fi
}

list() {
  local current
  current=$(env_get APP_VERSION)
  printf '%-22s %-28s %-10s %-9s %s\n' WHEN VERSION REVISION ACTION IMAGES
  while read -r when version revision action; do
    local images=missing
    docker image inspect "copilot-backend:$version" >/dev/null 2>&1 && images=present
    printf '%-22s %-28s %-10s %-9s %s%s\n' "$when" "$version" "$revision" "$action" "$images" "$([ "$version" = "$current" ] && echo '  <- running')"
  done < .deploy/history
}

main "$@"
