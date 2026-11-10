#!/usr/bin/env bash
# Sets one value in .env (adds the key if it is missing):  ./scripts/set-env.sh SMTP_HOST smtp.example.com
# Apply it with: docker compose up -d   (only containers whose configuration changed are recreated)
set -euo pipefail
. "$(dirname "$0")/lib.sh"
need_env
[ $# -eq 2 ] || die "Usage: $0 KEY VALUE"
printf '%s' "$1" | grep -Eq '^[A-Z][A-Z0-9_]*$' || die "'$1' is not a valid setting name"
case "$2" in *$'\n'*) die "Values cannot contain line breaks" ;; esac
env_set "$1" "$2"
chmod 600 .env
log "$1 updated"
