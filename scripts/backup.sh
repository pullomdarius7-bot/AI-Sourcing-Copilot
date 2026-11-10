#!/usr/bin/env bash
# Backs up everything needed to rebuild the system on another server:
#   db.dump             PostgreSQL custom-format dump (schema, data, grants, row-level security policies)
#   minio_data.tar.gz   document store (MinIO volume)
#   uploads.tar.gz      local-disk document fallback volume
#   env                 the .env file (secrets: JWT, MFA encryption key, database and MinIO passwords)
#   history             release history
# The database is dumped first and the files second, so every document the dump refers to is in the file backup.
#   ./scripts/backup.sh                       full backup
#   ./scripts/backup.sh --db-only             database only (used hourly and before each deployment)
#   ./scripts/backup.sh --label <name>        suffix for the archive name
# Settings in .env: BACKUP_DIR, BACKUP_KEEP_DAYS (14), BACKUP_PASSPHRASE_FILE (GPG AES-256 encryption),
#                   BACKUP_REMOTE (rsync target, e.g. backup@host:/srv/backups/copilot/)
set -Eeuo pipefail
. "$(dirname "$0")/lib.sh"

work=""  # global: used by the EXIT trap

main() {
  local db_only=0 label="" arg
  need_env
  while [ $# -gt 0 ]; do
    arg=$1; shift
    case "$arg" in
      --db-only) db_only=1 ;;
      --label) label=$(printf '%s' "${1:-}" | tr -c 'A-Za-z0-9._-' '_'); shift || true ;;
      *) die "Unknown option $arg" ;;
    esac
  done

  local dir keep passfile remote user db stamp name archive
  dir=$(env_get BACKUP_DIR "$ROOT/backups")
  keep=$(env_get BACKUP_KEEP_DAYS 14)
  passfile=$(env_get BACKUP_PASSPHRASE_FILE)
  remote=$(env_get BACKUP_REMOTE)
  user=$(env_get POSTGRES_USER copilot)
  db=$(env_get POSTGRES_DB copilot)
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  name="copilot-$stamp"
  if [ "$db_only" = 1 ]; then name="$name-db"; fi
  name="$name${label:+-$label}"
  umask 077
  mkdir -p "$dir"
  work="$dir/$name"
  mkdir "$work"
  trap 'rm -rf "$work"' EXIT

  [ -n "$(docker compose ps -q db 2>/dev/null)" ] || die "The database container is not running."
  log "Dumping database $db"
  docker compose exec -T db pg_dump -U "$user" -d "$db" -Fc --no-password > "$work/db.dump"
  docker compose exec -T db pg_restore --list < "$work/db.dump" > /dev/null || die "The dump is not readable."
  [ "$(stat -c %s "$work/db.dump")" -gt 1024 ] || die "The dump is suspiciously small."

  if [ "$db_only" = 0 ]; then
    local volume
    for volume in minio_data uploads; do
      if docker volume inspect "${PROJECT}_$volume" >/dev/null 2>&1; then
        log "Archiving volume $volume"
        docker run --rm --entrypoint tar -v "${PROJECT}_$volume:/data:ro" -v "$work:/backup" postgres:16-alpine \
          czf "/backup/$volume.tar.gz" -C /data .
        docker run --rm --entrypoint chown -v "$work:/backup" postgres:16-alpine "$(id -u):$(id -g)" "/backup/$volume.tar.gz"
      fi
    done
    cp .env "$work/env"
    [ -f .deploy/history ] && cp .deploy/history "$work/history"
  fi
  {
    echo "created=$stamp"
    echo "kind=$([ "$db_only" = 1 ] && echo db-only || echo full)"
    echo "app_version=$(env_get APP_VERSION unknown)"
    echo "host=$(hostname)"
  } > "$work/MANIFEST"
  (cd "$work" && sha256sum -- * > SHA256SUMS)

  archive="$dir/$name.tar"
  tar -C "$dir" -cf "$archive" "$name"
  rm -rf "$work"
  trap - EXIT
  if [ -n "$passfile" ]; then
    [ -r "$passfile" ] || die "BACKUP_PASSPHRASE_FILE ($passfile) is not readable."
    gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$passfile" --symmetric --cipher-algo AES256 \
      -o "$archive.gpg" "$archive"
    rm -f "$archive"
    archive="$archive.gpg"
  elif [ "$db_only" = 0 ]; then
    warn "This backup contains the .env secrets and is not encrypted (set BACKUP_PASSPHRASE_FILE in .env)."
  fi
  log "Backup written: $archive ($(du -h "$archive" | cut -f1))"

  if [ -n "$remote" ]; then
    log "Copying to $remote"
    rsync -a --partial -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes" "$archive" "$remote" \
      || die "Off-site copy failed. The local backup is intact."
  fi

  # Retention: hourly database dumps for 2 days, pre-deploy dumps and full backups for BACKUP_KEEP_DAYS.
  find "$dir" -maxdepth 1 -type f -name 'copilot-*-hourly.tar*' -mmin +2880 -delete
  find "$dir" -maxdepth 1 -type f -name 'copilot-*.tar*' -mtime "+$keep" -delete
  if [ "$db_only" = 0 ]; then date -u +%s > "$dir/last-full-success"; fi
  date -u +%s > "$dir/last-success"
}

main "$@"
