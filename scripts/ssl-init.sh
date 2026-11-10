#!/usr/bin/env bash
# Obtains the first Let's Encrypt certificate for DOMAIN through the nginx container (HTTP-01, webroot).
# nginx cannot start without certificate files, so a short-lived self-signed placeholder is created first and
# replaced by the real certificate. Renewal is automatic afterwards (certbot container + nginx reload).
#   ./scripts/ssl-init.sh                 issue the certificate (no-op if a real one exists)
#   ./scripts/ssl-init.sh --placeholder   only make sure nginx has something to start with
#   ACME_STAGING=1 ./scripts/ssl-init.sh  use Let's Encrypt's staging CA (testing; not trusted by browsers)
set -euo pipefail
. "$(dirname "$0")/lib.sh"
need_env

MODE=${1:-issue}
DOMAIN=$(env_get DOMAIN)
ACME_EMAIL=$(env_get ACME_EMAIL)
[ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] || die "Set DOMAIN in .env first (./scripts/configure-prod.sh <domain> <email>)"
has_proxy || die "The nginx overlay is not enabled (COMPOSE_FILE in .env). Run ./scripts/configure-prod.sh"
LIVE=/etc/letsencrypt/live/$DOMAIN

certbot_sh() { docker compose run --rm --no-deps -T --entrypoint sh certbot -c "$1"; }

state() {  # real | placeholder | none
  certbot_sh "if [ -f $LIVE/.placeholder ]; then echo placeholder; elif [ -f $LIVE/fullchain.pem ]; then echo real; else echo none; fi" | tail -n1
}

make_placeholder() {
  log "Creating a self-signed placeholder certificate for $DOMAIN"
  certbot_sh "mkdir -p $LIVE && openssl req -x509 -nodes -newkey rsa:2048 -days 2 -subj '/CN=$DOMAIN' \
    -keyout $LIVE/privkey.pem -out $LIVE/fullchain.pem 2>/dev/null && touch $LIVE/.placeholder"
}

current=$(state)
if [ "$current" = "none" ]; then
  make_placeholder
  current=placeholder
fi
if [ "$MODE" = "--placeholder" ]; then
  log "Certificate state: $current"
  exit 0
fi
if [ "$current" = "real" ]; then
  log "A certificate for $DOMAIN already exists; renewal is automatic. Nothing to do."
  exit 0
fi
[ -n "$ACME_EMAIL" ] || die "Set ACME_EMAIL in .env"

log "Starting nginx so Let's Encrypt can reach http://$DOMAIN/.well-known/acme-challenge/"
docker compose up -d --wait nginx

log "Requesting the certificate"
certbot_sh "rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf"
staging=""
[ "${ACME_STAGING:-0}" = "1" ] && staging="--staging"
if docker compose run --rm --no-deps -T --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
     -d "$DOMAIN" --email "$ACME_EMAIL" --agree-tos --no-eff-email --non-interactive --keep-until-expiring $staging; then
  docker compose exec -T nginx nginx -s reload
  log "Certificate installed for https://$DOMAIN"
else
  make_placeholder
  docker compose exec -T nginx nginx -s reload || true
  die "Let's Encrypt could not issue the certificate. Check that $DOMAIN resolves to this server and that port 80 is reachable, then run this script again. The site keeps running with the placeholder certificate."
fi
