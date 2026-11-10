#!/usr/bin/env sh
# Generates .env with fresh random secrets. Refuses to overwrite an existing file.
set -eu
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  echo ".env already exists - leaving it untouched."
  exit 0
fi
rand_hex() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
fernet_key() { head -c 32 /dev/urandom | base64 | tr '+/' '-_'; }
sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand_hex 24)|" \
  -e "s|^APP_DB_PASSWORD=.*|APP_DB_PASSWORD=$(rand_hex 24)|" \
  -e "s|^JWT_SECRET=.*|JWT_SECRET=$(rand_hex 48)|" \
  -e "s|^MFA_ENCRYPTION_KEY=.*|MFA_ENCRYPTION_KEY=$(fernet_key)|" \
  .env.example > .env
chmod 600 .env
echo "Created .env with generated secrets."
