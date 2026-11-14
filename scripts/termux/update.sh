#!/data/data/com.termux/files/usr/bin/bash
# Get the latest code, back up, update and restart.   bash scripts/termux/update.sh
. "$(dirname "$0")/common.sh"
require_termux
load_env
say "Backing up first"
bash "$REPO_DIR/scripts/termux/backup.sh"
bash "$REPO_DIR/scripts/termux/stop.sh" --keep-db
say "Downloading the latest code"
git -C "$REPO_DIR" pull --ff-only
install_backend
start_db
migrate
build_frontend
bash "$REPO_DIR/scripts/termux/start.sh"
