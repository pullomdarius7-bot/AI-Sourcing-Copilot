#!/usr/bin/env bash
# Shared helpers for the operations scripts. Source it: . "$(dirname "$0")/lib.sh"
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
PROJECT=ai-sourcing-copilot

log()  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
warn() { log "WARNING: $*" >&2; }
die()  { log "ERROR: $*" >&2; exit 1; }

# Read one value from .env without sourcing it (values may contain spaces and shell characters).
env_get() {
  local value=""
  [ -f "$ROOT/.env" ] && value=$(sed -n "s/^$1=//p" "$ROOT/.env" | tail -n1)
  printf '%s' "${value:-${2:-}}"
}

# Set or add KEY=VALUE in .env.
env_set() {
  local key=$1 value=$2 escaped
  escaped=$(printf '%s' "$value" | sed 's/[&|\\]/\\&/g')
  if grep -q "^$key=" "$ROOT/.env"; then
    sed -i "s|^$key=.*|$key=$escaped|" "$ROOT/.env"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ROOT/.env"
  fi
}

need_env() { [ -f "$ROOT/.env" ] || die "No .env here. Run ./scripts/init-env.sh (and ./scripts/configure-prod.sh on a server)."; }

# True when the nginx/certbot overlay is part of the compose project.
has_proxy() { docker compose config --services 2>/dev/null | grep -qx nginx; }

# Services that should be running (everything enabled, minus the one-shot migration).
long_running_services() { docker compose config --services | grep -vx migrate; }

# Database revision as alembic reports it, e.g. 0006 (empty if it cannot be read).
alembic_current() {
  docker compose run --rm --no-deps -T migrate alembic current 2>/dev/null | awk '/^[0-9a-zA-Z_]+( \(head\))?$/ {print $1}' | tail -n1
}
