#!/data/data/com.termux/files/usr/bin/bash
# Stop the website, worker, API and database.   bash scripts/termux/stop.sh
# (add --keep-db to leave the database running)
. "$(dirname "$0")/common.sh"
for name in web worker api; do
  if pid_alive "$name"; then
    kill "$(cat "$RUN/$name.pid")" 2>/dev/null || true
    for _ in $(seq 1 10); do pid_alive "$name" || break; sleep 1; done
    if pid_alive "$name"; then kill -9 "$(cat "$RUN/$name.pid")" 2>/dev/null || true; fi
    echo "$name stopped."
  fi
  rm -f "${RUN:?}/${name:?}.pid"
done
if [ "${1:-}" != "--keep-db" ] && db_running; then
  pg_ctl -m fast stop >/dev/null && echo "database stopped."
fi
if command -v termux-wake-unlock >/dev/null; then termux-wake-unlock; fi
