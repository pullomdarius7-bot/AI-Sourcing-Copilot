# Monitoring

Start with `make monitoring` (compose profile `monitoring`). Both services listen on 127.0.0.1 only.

| Service | URL | Notes |
| --- | --- | --- |
| Prometheus v2.55 | http://127.0.0.1:9090 | scrapes `backend:8000/metrics` every 15 s with `METRICS_TOKEN`; alert rules in `infra/monitoring/prometheus/alerts.yml` |
| Grafana 11.3 | http://127.0.0.1:3001 | user `admin`, password `GRAFANA_ADMIN_PASSWORD`; dashboard *AI Sourcing Copilot* is provisioned and read-only |

`./scripts/check-monitoring.sh` verifies that Prometheus sees the API as up, the alert rules are loaded and Grafana
serves the dashboard (CI runs it).

## Metrics

Process metrics (each API worker process writes a snapshot at most once a second; a scrape merges all live processes):

| Metric | Labels |
| --- | --- |
| `copilot_http_requests_total` | method, route (template, never the raw path), status |
| `copilot_http_request_duration_seconds` | method, route |
| `copilot_agent_answers_total` | mode, status |
| `copilot_agent_answer_duration_seconds` | mode |
| `copilot_agent_tool_calls_total` | tool, status |
| `copilot_agent_safety_events_total` | event (refused, model_output_rejected, prompt_injection_pattern_in_question, instruction_like_text_in_S…) |
| `copilot_llm_tokens_total` | provider, direction |

Database gauges, read at scrape time (exact across processes and restarts, aggregated over all organizations, no tenant
labels): `copilot_jobs{kind,status}`, `copilot_jobs_oldest_queued_seconds`, `copilot_documents{status}`,
`copilot_agent_runs_24h{mode,status}`, `copilot_agent_feedback_7d{rating}`, `copilot_knowledge_chunks`,
`copilot_database_up`.

## Alerts

| Alert | Condition |
| --- | --- |
| CopilotApiDown | scrape failing for 2 minutes |
| CopilotDatabaseUnavailable | API cannot query PostgreSQL for 2 minutes |
| CopilotHighErrorRate | more than 5 % of requests are 5xx for 10 minutes |
| CopilotSlowApi | p95 latency (excluding the assistant) above 2 s for 10 minutes |
| CopilotJobBacklog | a due job waiting more than 15 minutes |
| CopilotJobsDeadLettered | jobs dead-lettered in the last hour |
| CopilotAssistantErrors | an assistant run failed in the last 15 minutes |
| CopilotAssistantModelRejections | more than 30 % of model answers rejected by grounding in an hour |
| CopilotAssistantSlow | p95 answer time above 30 s |

Alerts are evaluated by Prometheus and shown in Prometheus and Grafana. To deliver them, add an Alertmanager and an
`alerting:` block to `prometheus.yml`.

## Security

`/metrics` is outside `/api`, so the web front end never proxies it, and the API port is bound to 127.0.0.1. It requires
`Authorization: Bearer $METRICS_TOKEN`; in production it is disabled when no token is set. Metrics carry no organization,
user or document identifiers.
