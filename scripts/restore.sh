#!/usr/bin/env bash
# Restores a backup made by scripts/backup.sh. THIS REPLACES the current database (and documents).
#   ./scripts/restore.sh <archive.tar | archive.tar.gpg>            database + documents
#   ./scripts/restore.sh <archive> --db-only                        database only
#   ./scripts/restore.sh <archive> --files-only                     documents only (database untouched)
#   ./scripts/restore.sh <archive> --with-env                       also replace .env with the one in the backup
#   ./scripts/restore.sh <archive> --yes                            do not ask for confirmation
# On a server without .env, the backup's .env is installed automatically (the MFA key and passwords must match the data).
set -Eeuo pipefail
. "$(dirname "$0")/lib.sh"

tmp=""  # global: the EXIT trap runs after main has returned

main() {
  local archive="" db_only=0 files_only=0 with_env=0 yes=0 arg
  for arg in "$@"; do
    case "$arg" in
      --db-only) db_only=1 ;;
      --files-only) files_only=1 ;;
      --with-env) with_env=1 ;;
      --yes) yes=1 ;;
      -*) die "Unknown option $arg" ;;
      *) archive=$arg ;;
    esac
  done
  [ -n "$archive" ] && [ -f "$archive" ] || die "Usage: $0 <backup archive> [--db-only | --files-only] [--with-env] [--yes]"
  [ "$db_only" = 0 ] || [ "$files_only" = 0 ] || die "--db-only and --files-only cannot be combined."
  archive=$(readlink -f "$archive")

  local plain src
  umask 077
  tmp=$(mktemp -d "${TMPDIR:-/var/tmp}/copilot-restore.XXXXXX")
  trap 'rm -rf "$tmp"' EXIT
  plain=$archive
  case "$archive" in
    *.gpg)
      local passfile
      passfile=${BACKUP_PASSPHRASE_FILE:-$(env_get BACKUP_PASSPHRASE_FILE /etc/copilot/backup.pass)}
      [ -r "$passfile" ] || die "Encrypted backup: put the passphrase in $passfile or set BACKUP_PASSPHRASE_FILE=<file>."
      plain="$tmp/archive.tar"
      gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$passfile" -o "$plain" -d "$archive" \
        || die "Decryption failed (wrong passphrase?)."
      ;;
  esac
  tar -C "$tmp" -xf "$plain"
  src=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d -name 'copilot-*' | head -n1)
  [ -n "$src" ] && [ -f "$src/db.dump" ] || die "This archive does not look like a backup (no db.dump)."
  (cd "$src" && sha256sum --quiet -c SHA256SUMS) || die "Checksum mismatch: the backup is damaged."
  log "Backup: $(tr '\n' ' ' < "$src/MANIFEST")"
  if [ "$db_only" = 0 ] && [ ! -f "$src/minio_data.tar.gz" ] && [ ! -f "$src/uploads.tar.gz" ]; then
    [ "$files_only" = 0 ] || die "This is a database-only backup; it contains no documents."
    log "This is a database-only backup; documents will not be touched."
    db_only=1
  fi

  if [ ! -f .env ]; then
    [ -f "$src/env" ] || die "No .env on this server and none in the backup. Restore a full backup, or recreate .env with the original secrets."
    install -m 600 "$src/env" .env
    log "Installed .env from the backup"
  elif [ "$with_env" = 1 ] && [ -f "$src/env" ]; then
    cp -p .env ".env.before-restore.$(date -u +%Y%m%d%H%M%S)"
    install -m 600 "$src/env" .env
    log "Replaced .env with the one from the backup (the old one was kept as .env.before-restore.*)"
  elif [ -f "$src/env" ] && [ "$(sed -n 's/^MFA_ENCRYPTION_KEY=//p' "$src/env")" != "$(env_get MFA_ENCRYPTION_KEY)" ]; then
    warn "MFA_ENCRYPTION_KEY in this .env differs from the backup's. Users with two-factor login will not be able to sign in. Use --with-env to take the backup's .env."
  fi

  if [ "$yes" != 1 ]; then
    local what="the database and all stored documents"
    if [ "$db_only" = 1 ]; then what="the database"; fi
    if [ "$files_only" = 1 ]; then what="all stored documents"; fi
    printf 'This replaces %s of %s on this server. Type RESTORE to continue: ' "$what" "$(env_get DOMAIN localhost)"
    local answer
    read -r answer
    [ "$answer" = "RESTORE" ] || die "Cancelled."
  fi

  local user db app_pw
  user=$(env_get POSTGRES_USER copilot)
  db=$(env_get POSTGRES_DB copilot)
  app_pw=$(env_get APP_DB_PASSWORD)
  printf '%s' "$app_pw" | grep -Eq '^[A-Za-z0-9_-]{16,128}$' || die "APP_DB_PASSWORD in .env is missing or malformed."

  log "Creating containers and volumes"
  docker compose create >/dev/null
  log "Stopping the application"
  local service
  for service in nginx frontend backend worker; do
    docker compose stop "$service" 2>/dev/null || true
  done
  docker compose up -d --wait db

  if [ "$files_only" = 0 ]; then
  log "Restoring database $db"
  docker compose exec -T db psql -v ON_ERROR_STOP=1 -q -U "$user" -d postgres -v pw="$app_pw" <<SQL
SELECT 'CREATE ROLE copilot_app LOGIN NOSUPERUSER NOBYPASSRLS'
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'copilot_app') \gexec
ALTER ROLE copilot_app WITH LOGIN PASSWORD :'pw' NOSUPERUSER NOBYPASSRLS;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "$db";
CREATE DATABASE "$db" OWNER "$user";
SQL
  docker compose exec -T db pg_restore -U "$user" -d "$db" --exit-on-error --no-password < "$src/db.dump"
  fi

  if [ "$db_only" = 0 ]; then
    docker compose stop minio 2>/dev/null || true
    local volume
    for volume in minio_data uploads; do
      [ -f "$src/$volume.tar.gz" ] || continue
      log "Restoring volume $volume"
      docker run --rm --entrypoint sh -v "${PROJECT}_$volume:/data" -v "$src:/backup:ro" postgres:16-alpine \
        -c "find /data -mindepth 1 -delete && tar xzf /backup/$volume.tar.gz -C /data"
    done
  fi

  log "Starting the stack (pending migrations are applied)"
  if has_proxy; then ./scripts/ssl-init.sh --placeholder; fi
  docker compose up -d --wait --wait-timeout "${WAIT_TIMEOUT:-900}"
  ./scripts/validate.sh || die "The restore finished but validation reports problems (see above)."
  log "Restore complete"
}

main "$@"
