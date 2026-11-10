#!/usr/bin/env bash
# Installs the operations schedule (run with sudo):
#   every 5 min  health probe            hourly   database dump (kept 2 days)
#   02:15 daily  full backup             Sun 04:30 restore test of the newest backup
#   Sun 05:00    prune unused images and build cache
#   sudo ./scripts/install-cron.sh [deploy-user]
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
RUN_AS=${1:-$(stat -c %U "$APP_DIR")}
install -d -m 750 -o "$RUN_AS" -g "$RUN_AS" /var/log/copilot

cat > /etc/cron.d/copilot <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * $RUN_AS cd $APP_DIR && ./scripts/healthcheck.sh >> /var/log/copilot/health.log 2>&1
5 * * * * $RUN_AS cd $APP_DIR && ./scripts/backup.sh --db-only --label hourly >> /var/log/copilot/backup.log 2>&1
15 2 * * * $RUN_AS cd $APP_DIR && ./scripts/backup.sh >> /var/log/copilot/backup.log 2>&1
30 4 * * 0 $RUN_AS cd $APP_DIR && ./scripts/verify-backup.sh >> /var/log/copilot/backup.log 2>&1
0 5 * * 0 $RUN_AS docker image prune -f >> /var/log/copilot/maintenance.log 2>&1; docker builder prune -f --filter until=168h >> /var/log/copilot/maintenance.log 2>&1
CRON
chmod 644 /etc/cron.d/copilot

cat > /etc/logrotate.d/copilot <<'ROTATE'
/var/log/copilot/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
ROTATE
systemctl enable --now cron >/dev/null 2>&1 || true
echo "Installed /etc/cron.d/copilot (runs as $RUN_AS from $APP_DIR) and /etc/logrotate.d/copilot."
