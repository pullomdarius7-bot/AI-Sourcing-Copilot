#!/usr/bin/env bash
# Post-deployment validation. Read-only unless VALIDATE_EMAIL / VALIDATE_PASSWORD are set (in the environment or
# .env), in which case it also signs in as that user and asks the assistant one reference question.
# Exit code 0 = no failures (warnings do not fail). Used by deploy.sh, rollback.sh and restore.sh.
set -uo pipefail
. "$(dirname "$0")/lib.sh"
need_env

FAILS=0
WARNS=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
note() { printf '  WARN  %s\n' "$*"; WARNS=$((WARNS + 1)); }
check() { local name=$1; shift; if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi; }

DOMAIN=$(env_get DOMAIN localhost)
PORT=$(env_get FRONTEND_PORT 3000)
LOCAL="http://127.0.0.1:$PORT"
if has_proxy; then
  PUBLIC="https://$DOMAIN"
  CURL_PUBLIC=(curl -s -k --noproxy '*' --max-time 20 --resolve "$DOMAIN:443:127.0.0.1")   # this server's nginx, whatever DNS says
else
  PUBLIC=$LOCAL
  CURL_PUBLIC=(curl -s --noproxy '*' --max-time 20)
fi
status() { "${CURL_PUBLIC[@]}" -o /dev/null -w '%{http_code}' "$@"; }

echo "Containers"
RUNNING=$(docker compose ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null)
for service in $(long_running_services); do
  line=$(printf '%s\n' "$RUNNING" | awk -v s="$service" '$1 == s' | head -n1)
  state=$(printf '%s' "$line" | awk '{print $2}')
  health=$(printf '%s' "$line" | awk '{print $3}')
  if [ "$state" != "running" ]; then fail "$service is ${state:-not created}"
  elif [ -n "$health" ] && [ "$health" != "healthy" ]; then fail "$service is running but $health"
  else pass "$service running${health:+ ($health)}"; fi
done

echo "Application"
check "API liveness (/api/v1/health)" docker compose exec -T backend python -c \
  "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).status == 200 else 1)"
check "API readiness, database reachable (/api/v1/ready)" docker compose exec -T backend python -c \
  "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=5).status == 200 else 1)"
check "Web front end on loopback ($LOCAL/api/healthz)" curl -fsS --noproxy '*' --max-time 10 "$LOCAL/api/healthz"
[ "$(status "$PUBLIC/login")" = "200" ] && pass "Sign-in page is served ($PUBLIC/login)" || fail "Sign-in page is not served ($PUBLIC/login)"
[ "$(status "$PUBLIC/api/auth/me")" = "401" ] && pass "API refuses anonymous requests (401)" || fail "/api/auth/me did not answer 401 to an anonymous request"
[ "$(status "$PUBLIC/api/rfqs")" = "401" ] && pass "Business data needs a session (401)" || fail "/api/rfqs did not answer 401 to an anonymous request"
code=$(status "$PUBLIC/metrics")
case "$code" in
  200) fail "/metrics is reachable through the web front end" ;;
  000) fail "The web front end did not answer the /metrics exposure check" ;;
  *) pass "Metrics are not exposed through the web front end ($code)" ;;
esac

revision=$(docker compose run --rm --no-deps -T migrate alembic current 2>/dev/null | tail -n1)
case "$revision" in
  *"(head)"*) pass "Database schema is at the latest revision ($revision)" ;;
  *) fail "Database schema is not at head (${revision:-unknown})" ;;
esac

echo "Metrics endpoint"
TOKEN=$(env_get METRICS_TOKEN)
code=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/metrics)
[ "$code" = "401" ] && pass "Refused without the token (401)" || fail "/metrics without a token answered $code, expected 401"
METRICS=$(curl -fsS --noproxy '*' --max-time 15 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/metrics 2>/dev/null)
printf '%s' "$METRICS" | grep -q '^copilot_database_up 1' && pass "Served with the token; database gauge is up" || fail "Metrics with the token are missing or the database gauge is down"
oldest=$(printf '%s\n' "$METRICS" | awk '/^copilot_jobs_oldest_queued_seconds/ {print int($2)}' | tail -n1)
if [ "${oldest:-0}" -gt 900 ]; then fail "A background job has been waiting ${oldest}s: is the worker processing?"; else pass "Background job queue is moving (oldest due job: ${oldest:-0}s)"; fi

if has_proxy; then
  echo "Reverse proxy and TLS"
  location=$(curl -s --noproxy '*' -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 -H "Host: $DOMAIN" http://127.0.0.1/)
  case "$location" in
    "301 https://$DOMAIN/"*) pass "HTTP redirects to HTTPS" ;;
    *) fail "HTTP did not redirect to https://$DOMAIN/ (got: $location)" ;;
  esac
  [ "$(status "$PUBLIC/healthz")" = "200" ] && pass "Proxy health endpoint ($PUBLIC/healthz)" || fail "$PUBLIC/healthz did not answer 200"
  HEADERS=$("${CURL_PUBLIC[@]}" -o /dev/null -D - "$PUBLIC/login" | tr -d '\r' | tr 'A-Z' 'a-z')
  for header in "strict-transport-security: max-age=31536000" "x-content-type-options: nosniff" "x-frame-options: deny" "referrer-policy:"; do
    printf '%s\n' "$HEADERS" | grep -q "^$header" && pass "Header ${header%%:*}" || fail "Missing header ${header%%:*}"
  done
  printf '%s\n' "$HEADERS" | grep -q '^server: nginx/' && fail "nginx version is disclosed" || pass "Server version not disclosed"
  CERT=$(echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" 2>/dev/null | openssl x509 2>/dev/null)
  if [ -z "$CERT" ]; then fail "No certificate is served on port 443"
  elif printf '%s' "$CERT" | openssl x509 -noout -issuer | grep -q "CN *= *$DOMAIN"; then note "Self-signed placeholder certificate. Run ./scripts/ssl-init.sh once $DOMAIN resolves to this server."
  elif ! printf '%s' "$CERT" | openssl x509 -noout -checkend $((14 * 86400)) >/dev/null; then note "The certificate expires within 14 days: $(printf '%s' "$CERT" | openssl x509 -noout -enddate). Check: docker compose logs certbot"
  else pass "Certificate valid, $(printf '%s' "$CERT" | openssl x509 -noout -enddate | sed 's/notAfter=/expires /')"; fi
  for proto in tls1 tls1_1; do
    if echo | openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" "-$proto" 2>/dev/null | grep -q 'BEGIN CERTIFICATE'; then fail "Legacy protocol $proto is accepted"; else pass "Legacy protocol $proto is refused"; fi
  done
fi

echo "Host"
if command -v ss >/dev/null 2>&1; then
  exposed=$(ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Ev '^(127\.|\[::1\]|\[::ffff:127\.)' | sed 's/.*://' | sort -un | tr '\n' ' ')
  unexpected=$(for p in $exposed; do case "$p" in 22|80|443|53|"$(env_get SSH_PORT 22)") ;; *) printf '%s ' "$p" ;; esac; done)
  [ -z "$unexpected" ] && pass "Only SSH/HTTP/HTTPS listen on public interfaces ($exposed)" || note "Ports listening on non-loopback interfaces: $unexpected(check they are firewalled)"
fi
usage=$(df --output=pcent "$ROOT" | tail -n1 | tr -dc '0-9')
[ "${usage:-0}" -lt 85 ] && pass "Disk usage ${usage}%" || note "Disk usage is ${usage}%"
BACKUP_DIR=$(env_get BACKUP_DIR "$ROOT/backups")
if [ -f "$BACKUP_DIR/last-full-success" ]; then
  age=$(( ($(date +%s) - $(cat "$BACKUP_DIR/last-full-success")) / 3600 ))
  [ "$age" -le 26 ] && pass "Last full backup ${age}h ago" || note "Last full backup is ${age}h old"
else
  note "No full backup has completed yet (./scripts/backup.sh, and ./scripts/install-cron.sh for the schedule)"
fi
[ "$(env_get SMTP_HOST)" = "mailpit" ] && has_proxy && note "Outgoing mail still goes to the local Mailpit capture; users receive nothing"

if docker compose config --services | grep -qx prometheus; then
  echo "Monitoring"
  curl -fsS --noproxy '*' --max-time 10 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22copilot-api%22%7D' 2>/dev/null | grep -q '"1"\]' \
    && pass "Prometheus scrapes the API" || note "Prometheus does not report the API as up yet (it scrapes every 15 s)"
  check "Grafana is healthy" curl -fsS --noproxy '*' --max-time 10 http://127.0.0.1:3001/api/health
fi

EMAIL=${VALIDATE_EMAIL:-$(env_get VALIDATE_EMAIL)}
PASSWORD=${VALIDATE_PASSWORD:-$(env_get VALIDATE_PASSWORD)}
if [ -n "$EMAIL" ] && [ -n "$PASSWORD" ]; then
  echo "Signed-in checks as $EMAIL"
  JAR=$(mktemp)
  trap 'rm -f "$JAR"' EXIT
  body=$(printf '{"email":"%s","password":"%s"}' "$EMAIL" "$PASSWORD")
  code=$("${CURL_PUBLIC[@]}" -c "$JAR" -o /dev/null -w '%{http_code}' -X POST "$PUBLIC/api/auth/login" -H 'content-type: application/json' -d "$body")
  if [ "$code" = "200" ]; then
    pass "Sign-in"
    "${CURL_PUBLIC[@]}" -b "$JAR" "$PUBLIC/api/auth/me" | grep -q "$EMAIL" && pass "Session works (/api/auth/me)" || fail "/api/auth/me did not return the user"
    [ "$("${CURL_PUBLIC[@]}" -b "$JAR" -o /dev/null -w '%{http_code}' "$PUBLIC/api/rfqs")" = "200" ] && pass "RFQ list" || fail "RFQ list"
    [ "$("${CURL_PUBLIC[@]}" -b "$JAR" -o /dev/null -w '%{http_code}' "$PUBLIC/api/documents")" = "200" ] && pass "Document list" || fail "Document list"
    "${CURL_PUBLIC[@]}" -b "$JAR" -X POST "$PUBLIC/api/agent/ask" -H 'content-type: application/json' -d '{"question":"What is DAP?"}' \
      | grep -q 'Delivered at Place' && pass "Assistant answers with a cited source" || fail "Assistant did not answer the reference question"
    "${CURL_PUBLIC[@]}" -b "$JAR" -o /dev/null -X POST "$PUBLIC/api/auth/logout"
  else
    fail "Sign-in as $EMAIL answered $code"
  fi
fi

echo
if [ "$FAILS" -gt 0 ]; then echo "VALIDATION FAILED: $FAILS failure(s), $WARNS warning(s)"; exit 1; fi
echo "VALIDATION PASSED with $WARNS warning(s)"
