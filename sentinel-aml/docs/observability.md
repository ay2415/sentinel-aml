# Observability

## 1. Signals

| Signal | Implementation | Where |
|---|---|---|
| Structured logs | JSON per line: timestamp, level, logger, message (PII-redacted), request ID, tenant, user, workflow ID, extra fields | `core/logging.py` |
| Correlation | `x-request-id` accepted if well-formed, else generated; echoed in responses, errors and audit records | `api/middleware.py` |
| Metrics | Prometheus at `/metrics` | `observability/metrics.py` |
| Traces | OpenTelemetry spans per agent step (`agent.triage`, `agent.investigation`, …); no-op unless `OTEL_ENABLED=true` | `observability/tracing.py` |
| LLM ledger | `llm_calls` table: model, prompt version and hash, tokens, estimated flag, cost, latency, attempts, status, schema validity, retrieved chunk IDs, redacted output preview | `ai/gateway.py` |
| Agent trace | `agent_steps`, `tool_invocations`, `workflow_runs` (tokens, cost, latency, failure reason) | Orchestrator |
| ML lineage | `predictions` + `model_versions` | `ml/scoring.py` |
| Data lineage | `ingestion_batches` quality reports | `data/ingestion.py` |
| Audit | Hash-chained `audit_log` | `services/audit.py` |

## 2. Metrics catalogue

| Metric | Type | Labels | Question it answers |
|---|---|---|---|
| `http_requests_total` | counter | method, route, status | Error rate per endpoint |
| `http_request_duration_seconds` | histogram | method, route | p50 / p95 per endpoint |
| `ml_predictions_total` | counter | model_version | Which model is serving |
| `ml_risk_score` | histogram | – | Score distribution shift |
| `ml_inference_seconds` | histogram | – | Model latency |
| `llm_calls_total` | counter | model, prompt, status | LLM failure rate by prompt |
| `llm_tokens_total` | counter | model, direction | Token volume |
| `llm_cost_usd_total` | counter | model | Spend |
| `llm_latency_seconds` | histogram | model | Model latency |
| `agent_step_seconds` | histogram | agent, status | Slow or degraded agents |
| `agent_tool_calls_total` | counter | agent, tool, status | Denied or invalid tool use |
| `rag_retrieval_seconds` | histogram | – | Retrieval latency |
| `rag_results_returned` | histogram | – | Empty-retrieval detection |
| `workflows_total` | counter | status | Share falling to manual review |
| `prompt_injection_flags_total` | counter | source | Manipulation attempts |

Route labels use route templates (`/api/v1/alerts/{alert_id}`), not raw paths, so label cardinality stays bounded.

## 3. Dashboards and alerts

- **Grafana dashboard** (`infrastructure/observability/grafana/dashboards/sentinel.json`), 12 panels: requests by status, API p95 by route, LLM calls, LLM p95, tokens per minute, 24-hour cost, agent step p95, tool calls, workflow outcomes, risk score quantiles, retrieval p95, injection detections.
- **Prometheus alerts** (`alerts.yml`):

| Alert | Meaning |
|---|---|
| HighApiErrorRate | 5xx >2% for 10 minutes |
| ApiLatencyP95High | p95 >500 ms, excluding investigations |
| LLMFailureRate | >5% failing calls; investigations are falling back to humans |
| WorkflowsFallingToManualReview | >10% of workflows per hour |
| LLMSpendSpike | >$50 per hour |
| PromptInjectionSurge | >20 detections per hour |
| RiskScoreDistributionShift | Median score shift over 6 hours |

## 4. Operational dashboards in the product

The Streamlit "Model and AI operations" page shows:

- Model versions and promotion gates.
- 30-day PSI drift.
- LLM usage by agent, model and prompt version.

"Audit trail" verifies the hash chain live.

## 5. Production path

API and workers → OpenTelemetry SDK → OTel Collector (attribute redaction processor) → Application Insights. Prometheus metrics go to Azure Monitor managed Prometheus, with Azure Managed Grafana on top.

## 6. Answering "what happened to this alert?"

1. `GET /api/v1/alerts/{id}`: score, model version, contributors, workflows.
2. `GET /api/v1/investigations/{workflow_id}`: every step, tool call, LLM call (model, prompt version, tokens, cost), injection flags.
3. `GET /api/v1/audit?entity_id={id}`: who did what and when, hash chain verified.
4. Logs filtered by `workflow_id` or `request_id`.
