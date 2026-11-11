#!/usr/bin/env bash
# Runs the k6 load test in a container (nothing to install).
#   LOADTEST_EMAIL=load@example.com LOADTEST_PASSWORD='...' ./scripts/loadtest.sh [smoke|load|stress]
# Target: BASE_URL, default http://127.0.0.1:<FRONTEND_PORT> (the application itself, bypassing nginx and its
# per-address rate limits). To test through the proxy from another machine: BASE_URL=https://your.domain
# A JSON summary is written to loadtest/results/.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

PROFILE=${1:-load}
case "$PROFILE" in smoke|load|stress) ;; *) die "Profile must be smoke, load or stress" ;; esac
[ -n "${LOADTEST_EMAIL:-}" ] && [ -n "${LOADTEST_PASSWORD:-}" ] || die "Set LOADTEST_EMAIL and LOADTEST_PASSWORD."
BASE_URL=${BASE_URL:-http://127.0.0.1:$(env_get FRONTEND_PORT 3000)}
mkdir -p loadtest/results
out="results/$(date -u +%Y%m%dT%H%M%SZ)-$PROFILE.json"

log "k6 $PROFILE against $BASE_URL"
docker run --rm -i --network host --user "$(id -u):$(id -g)" \
  -e BASE_URL="$BASE_URL" -e PROFILE="$PROFILE" -e LOADTEST_EMAIL -e LOADTEST_PASSWORD \
  -e AGENT_PER_MINUTE="${AGENT_PER_MINUTE:-10}" -e INSECURE_TLS="${INSECURE_TLS:-0}" \
  -v "$ROOT/loadtest:/work" -w /work grafana/k6:0.55.0 run --summary-export "$out" k6.js
log "Summary: loadtest/$out"
