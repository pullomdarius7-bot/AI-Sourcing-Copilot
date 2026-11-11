#!/usr/bin/env bash
# Lightweight health probe for cron (every 5 minutes). Exit 0 = healthy. Failures go to syslog (tag copilot-health)
# and, when ALERT_WEBHOOK_URL is set in .env, are posted there as {"text": "..."} (ntfy, Mattermost, Slack-compatible).
set -uo pipefail
. "$(dirname "$0")/lib.sh"
need_env

problems=()
DOMAIN=$(env_get DOMAIN localhost)
PORT=$(env_get FRONTEND_PORT 3000)

if has_proxy; then
  curl -fsS -k --noproxy '*' --max-time 15 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/healthz" >/dev/null 2>&1 || problems+=("https://$DOMAIN/healthz is not answering")
else
  curl -fsS --noproxy '*' --max-time 15 "http://127.0.0.1:$PORT/api/healthz" >/dev/null 2>&1 || problems+=("web front end is not answering")
fi
curl -fsS --noproxy '*' --max-time 15 http://127.0.0.1:8000/api/v1/ready >/dev/null 2>&1 || problems+=("API is not ready (database unreachable?)")
for service in db backend worker frontend; do
  [ -n "$(docker compose ps -q --status running "$service" 2>/dev/null)" ] || problems+=("container $service is not running")
done
usage=$(df --output=pcent "$ROOT" | tail -n1 | tr -dc '0-9')
[ "${usage:-0}" -lt 90 ] || problems+=("disk usage ${usage}%")
BACKUP_DIR=$(env_get BACKUP_DIR "$ROOT/backups")
if [ -f "$BACKUP_DIR/last-full-success" ]; then
  age=$(( ($(date +%s) - $(cat "$BACKUP_DIR/last-full-success")) / 3600 ))
  [ "$age" -le 30 ] || problems+=("last full backup is ${age}h old")
fi

if [ "${#problems[@]}" -eq 0 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ok"
  rm -f "$ROOT/.deploy/health-alerted" 2>/dev/null
  exit 0
fi
message="$DOMAIN unhealthy: $(printf '%s; ' "${problems[@]}" | sed 's/; $//')"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $message"
logger -t copilot-health -p daemon.err "$message" 2>/dev/null || true
WEBHOOK=$(env_get ALERT_WEBHOOK_URL)
# one notification per incident, not one every five minutes
if [ -n "$WEBHOOK" ] && [ ! -f "$ROOT/.deploy/health-alerted" ]; then
  mkdir -p "$ROOT/.deploy"
  payload=$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')
  curl -fsS --max-time 10 -X POST -H 'content-type: application/json' -d "{\"text\":\"$payload\"}" "$WEBHOOK" >/dev/null 2>&1 && touch "$ROOT/.deploy/health-alerted"
fi
exit 1
