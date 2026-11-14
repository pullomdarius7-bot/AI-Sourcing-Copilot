#!/data/data/com.termux/files/usr/bin/bash
# One-time setup of AI Sourcing Copilot on Android (Termux). Safe to run again: finished steps are skipped.
#   bash scripts/termux/setup.sh
. "$(dirname "$0")/common.sh"
require_termux

say "Step 1/7: Installing Termux packages"
pkg update -y
pkg install -y git python nodejs-lts postgresql rust clang make binutils pkg-config libffi openssl \
  libjpeg-turbo python-cryptography python-pillow termux-tools

mkdir -p "$APP_HOME" "$LOGS" "$RUN" "$BACKUPS" "$APP_HOME/uploads"
chmod 700 "$APP_HOME"

say "Step 2/7: Creating your private settings file ($ENV_FILE)"
if [ -f "$ENV_FILE" ]; then
  echo "Already exists: keeping it."
else
  password() { python -c "import secrets,string; a=string.ascii_letters+string.digits; print(''.join(secrets.choice(a) for _ in range(32)))"; }
  APP_DB_PASSWORD="$(password)"
  DB_ADMIN_PASSWORD="$(password)"
  JWT_SECRET="$(python -c "import secrets; print(secrets.token_urlsafe(48))")"
  MFA_ENCRYPTION_KEY="$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
  umask 077
  cat > "$ENV_FILE" <<ENV
# AI Sourcing Copilot settings for this phone. Keep this file private: it holds passwords and keys.
# After changing anything here, restart:  bash scripts/termux/stop.sh && bash scripts/termux/start.sh
ENVIRONMENT=production
APP_BASE_URL=http://localhost:3000

# Database (PostgreSQL on this phone)
DB_ADMIN_PASSWORD=$DB_ADMIN_PASSWORD
APP_DB_PASSWORD=$APP_DB_PASSWORD
DATABASE_URL=postgresql+asyncpg://copilot_app:$APP_DB_PASSWORD@127.0.0.1:5432/copilot
MIGRATION_DATABASE_URL=postgresql+asyncpg://copilot:$DB_ADMIN_PASSWORD@127.0.0.1:5432/copilot

# Security keys (generated for you; never share them)
JWT_SECRET=$JWT_SECRET
MFA_ENCRYPTION_KEY=$MFA_ENCRYPTION_KEY

# Files, email and background work on this phone
STORAGE_BACKEND=local
STORAGE_LOCAL_DIR=$APP_HOME/uploads
MINIO_ENDPOINT=
SCAN_BACKEND=builtin
EMAIL_BACKEND=console
REQUIRE_EMAIL_VERIFICATION=false
WORKER_HEARTBEAT_FILE=$APP_HOME/run/worker-heartbeat
METRICS_DIR=$APP_HOME/run/metrics

# Website
API_INTERNAL_URL=http://127.0.0.1:8000
COOKIE_SECURE=false
NEXT_TELEMETRY_DISABLED=1

# Back office: the site only listens on this phone, so two-factor sign-in is optional here.
# Set to true (and turn on two-factor sign-in) before putting the site on the internet.
PLATFORM_ADMIN_REQUIRES_MFA=false

# --- Optional: fill in to switch features on, then restart ------------------------------------------
# AI analysis (without a key, analyses use the built-in rules only)
AI_PROVIDER_ORDER=anthropic,openai
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
# AI web sourcing (finding suppliers on the internet), e.g. SEARCH_PROVIDER=brave with BRAVE_API_KEY
SEARCH_PROVIDER=
BRAVE_API_KEY=
# Supplier invitations are held until your business postal address is set (anti-spam law)
INVITE_SENDER_POSTAL_ADDRESS=
# Payments
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
ENV
  umask 022
  echo "Created. Your passwords and keys were generated automatically."
fi
load_env

say "Step 3/7: Setting up the database"
if [ -f "$PGDATA/PG_VERSION" ]; then
  echo "Database files already exist."
else
  pwfile="$APP_HOME/.pw"
  printf '%s\n' "$DB_ADMIN_PASSWORD" > "$pwfile"
  initdb -D "$PGDATA" -U copilot --auth=scram-sha-256 --pwfile="$pwfile" -E UTF8 >/dev/null
  rm -f "${pwfile:?}"
fi
if ! grep -q "^# copilot settings" "$PGDATA/postgresql.conf"; then
  # Only programs on this phone may connect; keep the socket in the app's own folder.
  printf "\n# copilot settings\nlisten_addresses = '127.0.0.1'\nunix_socket_directories = '%s'\n" "$RUN" >> "$PGDATA/postgresql.conf"
fi
start_db
if ! PGPASSWORD="$DB_ADMIN_PASSWORD" psql -h 127.0.0.1 -U copilot -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='copilot'" | grep -q 1; then
  PGPASSWORD="$DB_ADMIN_PASSWORD" createdb -h 127.0.0.1 -U copilot copilot
fi
echo "Database ready."

say "Step 4/7: Python packages"
install_backend

say "Step 5/7: Database structure"
migrate

say "Step 6/7: Website"
build_frontend

say "Step 7/7: Checking the installation"
(cd "$REPO_DIR/backend" && "$VENV/bin/python" -c "import app.main" && echo "Backend loads correctly.")

say "Setup finished."
cat <<MSG
Next:
  1. Start everything:              bash scripts/termux/start.sh
  2. Open in your phone's browser:  http://localhost:3000   and register your account
  3. Make yourself the admin:       bash scripts/termux/ops.sh grant-platform-admin YOUR@EMAIL
MSG
