#!/data/data/com.termux/files/usr/bin/bash
# Back up the database, uploaded files and settings to ~/.copilot/backups (keeps the last 14 of each).
#   bash scripts/termux/backup.sh
. "$(dirname "$0")/common.sh"
load_env
start_db
mkdir -p "$BACKUPS"
stamp="$(date +%Y%m%d-%H%M%S)"
PGPASSWORD="$DB_ADMIN_PASSWORD" pg_dump -h 127.0.0.1 -U copilot -Fc copilot > "$BACKUPS/db-$stamp.dump"
tar -czf "$BACKUPS/uploads-$stamp.tar.gz" -C "$APP_HOME" uploads
cp "$ENV_FILE" "$BACKUPS/env-$stamp"
chmod 600 "$BACKUPS/env-$stamp"
for pattern in 'db-*.dump' 'uploads-*.tar.gz' 'env-*'; do
  find "$BACKUPS" -maxdepth 1 -name "$pattern" -type f | sort -r | tail -n +15 | xargs -r rm -f --
done
say "Backup saved in $BACKUPS ($stamp)"
echo "To keep a copy off the phone: run termux-setup-storage once, then copy the files to ~/storage/downloads"
