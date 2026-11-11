#!/usr/bin/env bash
# Turns a generated .env into a production configuration: public URL, secure cookies, nginx + TLS overlay,
# monitoring and logging profiles, backup location. Existing secrets are never changed. Safe to run again.
#   ./scripts/configure-prod.sh copilot.example.com ops@example.com
set -euo pipefail
. "$(dirname "$0")/lib.sh"

DOMAIN=${1:-}
ACME_EMAIL=${2:-}
[ -n "$DOMAIN" ] && [ -n "$ACME_EMAIL" ] || die "Usage: $0 <domain> <email for Let's Encrypt notices>"
printf '%s' "$DOMAIN" | grep -Eq '^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$' || die "'$DOMAIN' is not a valid host name"
printf '%s' "$ACME_EMAIL" | grep -Eq '^[^@ ]+@[^@ ]+\.[^@ ]+$' || die "'$ACME_EMAIL' is not a valid email address"

[ -f .env ] || ./scripts/init-env.sh
./scripts/upgrade-env.sh

env_set DOMAIN "$DOMAIN"
env_set ACME_EMAIL "$ACME_EMAIL"
env_set APP_BASE_URL "https://$DOMAIN"
env_set COOKIE_SECURE true
env_set FRONTEND_BIND 127.0.0.1
env_set COMPOSE_FILE "docker-compose.yml:docker-compose.prod.yml"
env_set COMPOSE_PROFILES "$(env_get COMPOSE_PROFILES monitoring,logging)"
env_set BACKUP_DIR "$(env_get BACKUP_DIR "$(dirname "$ROOT")/backups")"
env_set BACKUP_KEEP_DAYS "$(env_get BACKUP_KEEP_DAYS 14)"
case "$(env_get EMAIL_FROM)" in
  *sourcing.local*) env_set EMAIL_FROM "AI Sourcing Copilot <no-reply@$DOMAIN>" ;;
esac
chmod 600 .env

log "Production settings written to .env for https://$DOMAIN"
if [ "$(env_get SMTP_HOST)" = "mailpit" ]; then
  warn "SMTP_HOST is still 'mailpit': verification, reset and RFQ emails are captured locally and reach nobody."
  warn "Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and SMTP_STARTTLS in .env to your mail relay before going live."
fi
if [ -z "$(env_get BACKUP_PASSPHRASE_FILE)" ]; then
  warn "BACKUP_PASSPHRASE_FILE is not set: backups (which contain .env secrets) will not be encrypted."
fi
