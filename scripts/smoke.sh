#!/usr/bin/env sh
# End-to-end smoke test through the public frontend port: register -> verify -> login -> me -> logout.
set -eu
BASE="${BASE_URL:-http://localhost:${FRONTEND_PORT:-3000}}"
MAIL="${MAIL_URL:-http://localhost:8025}"
EMAIL="smoke-$(date +%s)@example.test"
PASSWORD="smoke-test-password-42"
JAR="$(mktemp)"; trap 'rm -f "$JAR"' EXIT

echo "-> frontend health";  curl -fsS "$BASE/api/healthz" >/dev/null
echo "-> register";         curl -fsS -X POST "$BASE/api/auth/register" -H 'content-type: application/json' \
  -d "{\"organization_name\":\"Smoke Co\",\"full_name\":\"Smoke Test\",\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" >/dev/null
sleep 2
echo "-> read verification mail"
MSG_ID=$(curl -fsS "$MAIL/api/v1/search?query=to:$EMAIL" | sed -n 's/.*"ID":"\([^"]*\)".*/\1/p' | head -n1)
TOKEN=$(curl -fsS "$MAIL/api/v1/message/$MSG_ID" | sed -n 's/.*token=\([A-Za-z0-9_-]*\).*/\1/p' | head -n1)
[ -n "$TOKEN" ] || { echo "no verification token found"; exit 1; }
echo "-> verify";           curl -fsS -X POST "$BASE/api/auth/verify-email" -H 'content-type: application/json' -d "{\"token\":\"$TOKEN\"}" >/dev/null
echo "-> login";            curl -fsS -c "$JAR" -X POST "$BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" >/dev/null
echo "-> me";               curl -fsS -b "$JAR" "$BASE/api/auth/me" | grep -q "$EMAIL"
echo "-> logout";           curl -fsS -b "$JAR" -c "$JAR" -X POST "$BASE/api/auth/logout" >/dev/null
CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/auth/me")
[ "$CODE" = "401" ] || { echo "expected 401 after logout, got $CODE"; exit 1; }
echo "SMOKE TEST PASSED"
