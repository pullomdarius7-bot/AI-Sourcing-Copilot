#!/data/data/com.termux/files/usr/bin/bash
# Owner tools. Examples:
#   bash scripts/termux/ops.sh grant-platform-admin you@example.com
#   bash scripts/termux/ops.sh revoke-platform-admin someone@example.com
#   bash scripts/termux/ops.sh list-platform-admins
#   bash scripts/termux/ops.sh health
. "$(dirname "$0")/common.sh"
load_env
start_db
cd "$REPO_DIR/backend"
exec "$VENV/bin/python" -m app.ops "$@"
