#!/usr/bin/env bash
# Proves that a backup can be restored, without touching the running system: the dump is loaded into a throwaway
# PostgreSQL container and the file archives are read end to end.
#   ./scripts/verify-backup.sh              newest full backup in BACKUP_DIR
#   ./scripts/verify-backup.sh <archive>
set -Eeuo pipefail
. "$(dirname "$0")/lib.sh"

tmp=""
container=""  # globals: the EXIT trap runs after main has returned

main() {
  local dir archive plain src
  need_env
  dir=$(env_get BACKUP_DIR "$ROOT/backups")
  archive=${1:-$(find "$dir" -maxdepth 1 -type f -name 'copilot-*.tar*' ! -name '*-db*' -printf '%T@ %p\n' | sort -n | tail -n1 | cut -d' ' -f2-)}
  [ -n "$archive" ] && [ -f "$archive" ] || die "No backup found in $dir"
  log "Verifying $archive"
  umask 077
  tmp=$(mktemp -d "${TMPDIR:-/var/tmp}/copilot-verify.XXXXXX")
  container="copilot-verify-$$"
  trap 'docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
  plain=$archive
  case "$archive" in
    *.gpg)
      plain="$tmp/archive.tar"
      gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$(env_get BACKUP_PASSPHRASE_FILE /etc/copilot/backup.pass)" \
        -o "$plain" -d "$archive" || die "Decryption failed."
      ;;
  esac
  tar -C "$tmp" -xf "$plain"
  src=$(find "$tmp" -mindepth 1 -maxdepth 1 -type d -name 'copilot-*' | head -n1)
  (cd "$src" && sha256sum --quiet -c SHA256SUMS) || die "Checksum mismatch."

  local f
  for f in minio_data.tar.gz uploads.tar.gz; do
    [ -f "$src/$f" ] && { tar tzf "$src/$f" > /dev/null || die "$f is damaged."; log "$f: $(tar tzf "$src/$f" | wc -l) entries"; }
  done

  docker run -d --name "$container" -e POSTGRES_PASSWORD=verify -e POSTGRES_USER=verify -e POSTGRES_DB=verify postgres:16-alpine >/dev/null
  local i
  for i in $(seq 1 60); do
    docker exec "$container" pg_isready -q -U verify -d verify && break
    sleep 1
    [ "$i" -lt 60 ] || die "The scratch database did not start."
  done
  sleep 2
  docker exec "$container" psql -q -U verify -d verify -c "CREATE ROLE copilot_app NOLOGIN" -c "CREATE ROLE \"$(env_get POSTGRES_USER copilot)\" NOLOGIN" >/dev/null 2>&1 || true
  docker exec -i "$container" pg_restore -U verify -d verify --no-owner --exit-on-error < "$src/db.dump"
  local summary
  summary=$(docker exec "$container" psql -At -U verify -d verify -c \
    "SELECT 'revision=' || (SELECT version_num FROM alembic_version) || ' tables=' || (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') || ' organizations=' || (SELECT count(*) FROM organizations) || ' users=' || (SELECT count(*) FROM users) || ' documents=' || (SELECT count(*) FROM documents) || ' rls_tables=' || (SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity)")
  log "Restored into a scratch database: $summary"
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$archive")" "$summary" >> "$dir/verify.log"
  date -u +%s > "$dir/last-verify-success"
  log "BACKUP VERIFIED"
}

main "$@"
