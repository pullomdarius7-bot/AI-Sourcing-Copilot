#!/usr/bin/env sh
# Verifies the monitoring profile: Prometheus scrapes the API successfully and Grafana serves the provisioned dashboard.
set -eu
cd "$(dirname "$0")/.."
GRAFANA_ADMIN_PASSWORD=$(sed -n 's/^GRAFANA_ADMIN_PASSWORD=//p' .env | tail -n1)
PROM=${PROM_URL:-http://127.0.0.1:9090}
GRAFANA=${GRAFANA_URL:-http://127.0.0.1:3001}
end=$(( $(date +%s) + ${MONITORING_TIMEOUT:-180} ))
echo "-> Prometheus scrapes the API"
until curl -fsS "$PROM/api/v1/query?query=up%7Bjob%3D%22copilot-api%22%7D" | grep -q '"value":\[[0-9.]*,"1"\]'; do
  [ "$(date +%s)" -lt "$end" ] || { echo "Prometheus never saw the API as up"; curl -fsS "$PROM/api/v1/targets" || true; exit 1; }
  sleep 5
done
curl -fsS "$PROM/api/v1/query?query=copilot_database_up" | grep -q '"1"\]' || { echo "database gauge missing"; exit 1; }
curl -fsS "$PROM/api/v1/rules" | grep -q 'CopilotApiDown' || { echo "alert rules not loaded"; exit 1; }
echo "-> Grafana dashboard provisioned"
until curl -fsS "$GRAFANA/api/health" | grep -q '"database": *"ok"'; do
  [ "$(date +%s)" -lt "$end" ] || { echo "Grafana not healthy"; exit 1; }
  sleep 3
done
curl -fsS -u "admin:$GRAFANA_ADMIN_PASSWORD" "$GRAFANA/api/dashboards/uid/copilot-overview" | grep -q '"title":"AI Sourcing Copilot"'
echo "MONITORING CHECK PASSED"
