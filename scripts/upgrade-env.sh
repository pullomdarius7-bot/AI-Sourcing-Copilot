#!/usr/bin/env sh
# Adds settings introduced after an .env was first generated, with fresh secrets. Existing values are never changed.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env yet - run ./scripts/init-env.sh"; exit 1; }
rand_hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
add() {  # name value
  if ! grep -q "^$1=" .env; then
    printf '%s=%s\n' "$1" "$2" >> .env
    echo "added $1"
  fi
}
add METRICS_TOKEN "$(rand_hex 32)"
add GRAFANA_ADMIN_PASSWORD "$(rand_hex 16)"
add AGENT_MODE auto
echo ".env is up to date."
