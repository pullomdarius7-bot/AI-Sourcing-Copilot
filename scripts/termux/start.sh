#!/data/data/com.termux/files/usr/bin/bash
# Start the database, the API, the background worker and the website.   bash scripts/termux/start.sh
. "$(dirname "$0")/common.sh"
require_termux
load_env
mkdir -p "$LOGS" "$RUN" "$METRICS_DIR"

if command -v termux-wake-lock >/dev/null; then termux-wake-lock; fi   # stops Android pausing the servers

say "Starting the database"
start_db

say "Applying any pending database updates"
(cd "$REPO_DIR/backend" && "$VENV/bin/alembic" upgrade head >>"$LOGS/migrate.log" 2>&1) \
  || die "Database update failed. See $LOGS/migrate.log"

start_bg() {  # name, directory, command...
  local name="$1" dir="$2"; shift 2
  if pid_alive "$name"; then echo "$name is already running."; return; fi
  (cd "$dir" && exec nohup "$@" >>"$LOGS/$name.log" 2>&1 </dev/null) &
  echo $! > "$RUN/$name.pid"
  echo "$name started (log: $LOGS/$name.log)"
}

[ -f "$REPO_DIR/frontend/.next/standalone/server.js" ] || die "The website is not built yet. Run: bash scripts/termux/setup.sh"

say "Starting the app"
start_bg api "$REPO_DIR/backend" "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000
start_bg worker "$REPO_DIR/backend" "$VENV/bin/python" -m app.worker
start_bg web "$REPO_DIR/frontend/.next/standalone" env HOSTNAME=127.0.0.1 PORT=3000 node server.js

printf 'Waiting for the app to answer'
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:3000/api/ready >/dev/null 2>&1; then
    printf '\n'
    say "Running. Open http://localhost:3000 in your phone's browser."
    echo "Stop:  bash scripts/termux/stop.sh      Check:  bash scripts/termux/status.sh"
    exit 0
  fi
  printf '.'
  sleep 2
done
printf '\n'
die "The app did not answer within 2 minutes. Run: bash scripts/termux/status.sh"
