#!/usr/bin/env sh
# End-to-end smoke test through the public frontend port: register -> verify -> login -> me -> logout.
set -eu
BASE="${BASE_URL:-http://localhost:${FRONTEND_PORT:-3000}}"
MAIL="${MAIL_URL:-http://localhost:8025}"
EMAIL="smoke-$(date +%s)@example.com"  # .test is a reserved TLD the API rejects; mail goes to Mailpit
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
echo "-> upload documents (clean file + EICAR test file)"
TMP_DIR="$(mktemp -d)"; trap 'rm -f "$JAR"; rm -rf "$TMP_DIR"' EXIT
printf 'Quotation\nSupplier: Smoke Metals\nCurrency: EUR\nItem: bracket | Qty 10 | Unit price 2.50\n' > "$TMP_DIR/quote-$$.txt"
printf '%s' 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > "$TMP_DIR/eicar-$$.txt"
upload() { curl -fsS -b "$JAR" -F "file=@$1;type=text/plain" "$BASE/api/documents" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p'; }
wait_for() {  # id, expected status, timeout seconds (ClamAV needs time to load signatures on first start)
  end=$(( $(date +%s) + $3 ))
  while [ "$(date +%s)" -lt "$end" ]; do
    st=$(curl -fsS -b "$JAR" "$BASE/api/documents/$1" | grep -o '"status":"[a-z]*"' | head -n1 | cut -d'"' -f4)
    [ "$st" = "$2" ] && return 0
    case "$st" in failed|quarantined|processed) [ "$st" = "$2" ] || { echo "document $1 ended as $st, expected $2"; return 1; } ;; esac
    sleep 3
  done
  echo "timed out waiting for document $1 to become $2"; return 1
}
GOOD_ID=$(upload "$TMP_DIR/quote-$$.txt"); BAD_ID=$(upload "$TMP_DIR/eicar-$$.txt")
[ -n "$GOOD_ID" ] && [ -n "$BAD_ID" ] || { echo "upload failed"; exit 1; }
wait_for "$GOOD_ID" processed "${DOC_TIMEOUT:-900}"
wait_for "$BAD_ID" quarantined "${DOC_TIMEOUT:-900}"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/documents/$BAD_ID/download")
[ "$CODE" = "409" ] || { echo "infected download should be blocked, got $CODE"; exit 1; }
curl -fsS -b "$JAR" "$BASE/api/documents/$GOOD_ID/download" | grep -q "Smoke Metals"
echo "-> requirements API reachable through the proxy"
curl -fsS -b "$JAR" "$BASE/api/extraction-runs" | grep -q '^\['
curl -fsS -b "$JAR" "$BASE/api/review/queue" | grep -q '"total"'
curl -fsS -b "$JAR" "$BASE/api/documents/$GOOD_ID/text" | grep -q "Smoke Metals"
echo "-> suppliers and RFQs"
SUP_ID=$(curl -fsS -b "$JAR" -X POST "$BASE/api/suppliers" -H 'content-type: application/json' \
  -d "{\"name\":\"Smoke Supplier $$\",\"contacts\":[{\"name\":\"Sam\",\"email\":\"sam@smoke-supplier.example.com\"}]}" \
  | sed -n 's/^{"id":"\([^"]*\)".*/\1/p')
[ -n "$SUP_ID" ] || { echo "supplier creation failed"; exit 1; }
curl -fsS -b "$JAR" "$BASE/api/suppliers/$SUP_ID/qualification" | grep -q '"quality_certification"'
RFQ_ID=$(curl -fsS -b "$JAR" -X POST "$BASE/api/rfqs" -H 'content-type: application/json' \
  -d '{"title":"Smoke RFQ"}' | sed -n 's/^{"id":"\([^"]*\)".*/\1/p')
[ -n "$RFQ_ID" ] || { echo "RFQ creation failed"; exit 1; }
curl -fsS -b "$JAR" -X POST "$BASE/api/rfqs/$RFQ_ID/items" -H 'content-type: application/json' \
  -d '{"statement":"Smoke test line","quantity":10,"unit":"pcs"}' | grep -q '"Smoke test line"'
curl -fsS -b "$JAR" "$BASE/api/rfqs/$RFQ_ID/pdf" | head -c 5 | grep -q '%PDF-'
curl -fsS -b "$JAR" "$BASE/api/suppliers/export" | grep -q "Smoke Supplier"
echo "-> quote analysis reference data"
RATE_ID=$(curl -fsS -b "$JAR" -X POST "$BASE/api/fx-rates" -H 'content-type: application/json' \
  -d "{\"base\":\"EUR\",\"quote\":\"USD\",\"rate\":\"1.0850\",\"valid_from\":\"$(date -u +%Y-%m-%d)\",\"source\":\"smoke $$\"}" \
  | sed -n 's/^{"id":"\([^"]*\)".*/\1/p')
[ -n "$RATE_ID" ] || { echo "exchange rate creation failed"; exit 1; }
curl -fsS -b "$JAR" "$BASE/api/unit-conversions/check?from_unit=lb&to_unit=kg" | grep -q '"factor":"0.45359237"'
curl -fsS -b "$JAR" "$BASE/api/units" | grep -q '"dimension":"mass"'
CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X DELETE "$BASE/api/fx-rates/$RATE_ID")
[ "$CODE" = "204" ] || { echo "exchange rate delete returned $CODE"; exit 1; }
curl -fsS -b "$JAR" "$BASE/api/rfqs/$RFQ_ID/recommendations" | grep -q '^\['
echo "-> sourcing assistant (deterministic, cited)"
ANSWER=$(curl -fsS -b "$JAR" -X POST "$BASE/api/agent/ask" -H 'content-type: application/json' -d '{"question":"What is DAP?"}')
echo "$ANSWER" | grep -q 'Delivered at Place' || { echo "assistant did not answer from the reference"; exit 1; }
echo "$ANSWER" | grep -q '"kind":"glossary"' || { echo "assistant answer has no source"; exit 1; }
RUN_ID=$(echo "$ANSWER" | sed -n 's/.*"run":{"id":"\([^"]*\)".*/\1/p')
[ -n "$RUN_ID" ] || { echo "assistant answer is not traceable to a run"; exit 1; }
curl -fsS -b "$JAR" "$BASE/api/agent/runs/$RUN_ID" | grep -q '"prompt_sha256"'
ANSWER=$(curl -fsS -b "$JAR" -X POST "$BASE/api/agent/ask" -H 'content-type: application/json' -d "{\"question\":\"What should we do next on $(curl -fsS -b "$JAR" "$BASE/api/rfqs/$RFQ_ID" | sed -n 's/.*"number":"\([^"]*\)".*/\1/p')?\"}")
echo "$ANSWER" | grep -q 'submit it for approval\|Before submitting' || { echo "assistant next actions missing"; exit 1; }
if [ -n "${METRICS_URL:-}" ]; then
  echo "-> metrics"
  TOKEN="${METRICS_TOKEN:-$(sed -n 's/^METRICS_TOKEN=//p' "$(dirname "$0")/../.env" 2>/dev/null)}"
  curl -fsS -H "Authorization: Bearer $TOKEN" "$METRICS_URL" | grep -q 'copilot_agent_answers_total'
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "$METRICS_URL")
  [ "$CODE" = "401" ] || { echo "metrics without a token should be refused, got $CODE"; exit 1; }
fi
echo "-> logout";           curl -fsS -b "$JAR" -c "$JAR" -X POST "$BASE/api/auth/logout" >/dev/null
CODE=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE/api/auth/me")
[ "$CODE" = "401" ] || { echo "expected 401 after logout, got $CODE"; exit 1; }
echo "SMOKE TEST PASSED"
