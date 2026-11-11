#!/usr/bin/env bash
# Forced command for the CI deploy key. In ~deploy/.ssh/authorized_keys:
#   command="/opt/copilot/app/scripts/ci-entry.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... github-actions
# The key can then only run: deploy <git-ref> | rollback [version] | status
set -euo pipefail
cd "$(dirname "$0")/.."
read -r action argument extra <<< "${SSH_ORIGINAL_COMMAND:-}"
[ -z "${extra:-}" ] || { echo "Too many arguments"; exit 2; }
if [ -n "${argument:-}" ] && ! printf '%s' "$argument" | grep -Eq '^[A-Za-z0-9._/-]{1,100}$'; then
  echo "Invalid argument"; exit 2
fi
case "${action:-}" in
  deploy)   [ -n "${argument:-}" ] || { echo "deploy needs a git ref"; exit 2; }
            exec ./scripts/deploy.sh "$argument" ;;
  rollback) if [ -n "${argument:-}" ]; then exec ./scripts/rollback.sh "$argument" --keep-db; else exec ./scripts/rollback.sh --keep-db; fi ;;
  status)   ./scripts/rollback.sh --list; exec docker compose ps ;;
  *)        echo "Allowed commands: deploy <git-ref> | rollback [version] | status"; exit 2 ;;
esac
