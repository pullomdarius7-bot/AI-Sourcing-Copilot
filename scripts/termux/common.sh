# Shared settings for the Termux scripts. Sourced, not run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_HOME="${COPILOT_HOME:-$HOME/.copilot}"     # everything that is not code lives here (never in git)
ENV_FILE="$APP_HOME/env"
PGDATA="$APP_HOME/pgdata"
VENV="$APP_HOME/venv"
LOGS="$APP_HOME/logs"
RUN="$APP_HOME/run"
BACKUPS="$APP_HOME/backups"
export PGDATA

say()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

require_termux() {
  case "${PREFIX:-}" in
    *com.termux*) ;;
    *) die "Run this inside the Termux app on Android." ;;
  esac
}

load_env() {
  [ -f "$ENV_FILE" ] || die "No settings file at $ENV_FILE. Run: bash scripts/termux/setup.sh"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
}

db_running() { pg_ctl status >/dev/null 2>&1; }

start_db() {
  if db_running; then return 0; fi
  mkdir -p "$LOGS"
  pg_ctl -l "$LOGS/postgres.log" -w start >/dev/null || die "PostgreSQL did not start. See $LOGS/postgres.log"
}

pid_alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

# Next.js has no native compiler for Android: make it use its WebAssembly one (only affects platforms without a
# native compiler, so it changes nothing elsewhere).
patch_next_for_android() {
  local f
  [ "$(uname -o 2>/dev/null)" = "Android" ] || return 0
  for f in "$REPO_DIR"/frontend/node_modules/next/dist/build/swc/index.js \
           "$REPO_DIR"/frontend/node_modules/next/dist/esm/build/swc/index.js; do
    if [ -f "$f" ]; then
      sed -i 's/async function loadBindings(useWasmBinary = false)/async function loadBindings(useWasmBinary = true)/' "$f"
    fi
  done
}

install_backend() {
  say "Installing the backend's Python packages (the first time takes 15-40 minutes: some are compiled on the phone)"
  [ -d "$VENV" ] || python -m venv --system-site-packages "$VENV"   # reuses Termux's cryptography and Pillow
  # Termux's own cryptography build, and plain uvicorn (without native extras), replace the server pins.
  grep -v -E '^(cryptography|uvicorn)' "$REPO_DIR/backend/requirements.txt" > "$APP_HOME/requirements-termux.txt"
  echo "uvicorn==$(sed -n 's/^uvicorn\[standard\]==//p' "$REPO_DIR/backend/requirements.txt")" >> "$APP_HOME/requirements-termux.txt"
  export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk)"   # needed to compile the Rust-based packages
  "$VENV/bin/pip" install --upgrade pip wheel
  "$VENV/bin/pip" install -r "$APP_HOME/requirements-termux.txt"
}

migrate() {
  say "Updating the database structure"
  (cd "$REPO_DIR/backend" && "$VENV/bin/alembic" upgrade head)
}

build_frontend() {
  say "Building the website (5-20 minutes on a phone)"
  cd "$REPO_DIR/frontend"
  npm ci --no-audit --no-fund
  npm install --no-save --no-audit --no-fund "@next/swc-wasm-nodejs@$(node -p "require('next/package.json').version")" \
    || warn "Could not pre-install the WebAssembly compiler; Next.js will download it during the build."
  patch_next_for_android
  NODE_OPTIONS="--max-old-space-size=${NODE_MEMORY_MB:-2048}" NEXT_TELEMETRY_DISABLED=1 npm run build
  # The standalone server is lighter than "next start"; it needs the static files copied next to it.
  local out="$REPO_DIR/frontend/.next/standalone"
  rm -rf "${out:?}/.next/static" "${out:?}/public"
  cp -r .next/static "$out/.next/static"
  if [ -d public ]; then cp -r public "$out/public"; fi
  cd "$REPO_DIR"
}
