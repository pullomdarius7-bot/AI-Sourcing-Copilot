#!/data/data/com.termux/files/usr/bin/bash
# Is everything running and healthy?   bash scripts/termux/status.sh
. "$(dirname "$0")/common.sh"
load_env
row() { printf '  %-10s %s\n' "$1" "$2"; }

say "Processes"
if db_running; then row database running; else row database "STOPPED (log: $LOGS/postgres.log)"; fi
for name in api worker web; do
  if pid_alive "$name"; then row "$name" running; else row "$name" "STOPPED (log: $LOGS/$name.log)"; fi
done

say "Website"
if curl -fsS http://127.0.0.1:3000/api/ready >/dev/null 2>&1; then
  row website "answering at http://localhost:3000"
else
  row website "NOT answering"
fi

say "Detailed health checks (same as Back office > Health)"
if db_running; then
  (cd "$REPO_DIR/backend" && "$VENV/bin/python" -m app.ops health) || true
else
  echo "  Start the app first: bash scripts/termux/start.sh"
fi
