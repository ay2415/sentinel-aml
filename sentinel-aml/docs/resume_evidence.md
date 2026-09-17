# Resume evidence

Only facts that were implemented and measured in this repository. Every number traces to `evaluation/reports/*.json` via `docs/evaluation.md`.

**Framing rule for bullets:** say "synthetic data", "local", and "deterministic reference agents" where relevant. Do not claim production deployment, real bank data, real Claude evaluation, Docker/Azure execution, or business impact in money.

## 1. What was built (verifiable in code)

- **End-to-end AML alert investigation platform** in Python: ~7,400 lines across application, tests, evaluation and dashboard; 20-table PostgreSQL + pgvector schema; 26 REST API operations across 25 paths (FastAPI, Pydantic).
- **Data engineering:**
  - Behavioural synthetic data generator: 3,000 customers, 771,818 transactions, 5 laundering typologies, 9 legitimate personas, 9 injected defect types.
  - Validation, cleaning, deduplication and quality gates.
  - Idempotent PostgreSQL COPY + upsert load.
  - Point-in-time feature engineering (33 features).
  - 6-rule monitoring engine with suppression.
- **Machine learning:**
  - XGBoost with Platt calibration, logistic baseline, isotonic comparison, Isolation Forest.
  - Temporal split, customer-grouped CV, out-of-fold threshold selection.
  - Promotion gates, SHA-256-verified model registry, per-prediction lineage and contributions, PSI drift monitoring.
- **RAG:**
  - 13-document knowledge base, heading-aware chunking.
  - Hybrid retrieval (pgvector cosine + BM25 + reciprocal rank fusion + reranking).
  - Tenant and clearance access control enforced inside the SQL query.
  - Document poisoning quarantine with four-eyes approval.
- **Agentic AI:**
  - 4 specialised agents (triage, investigation with 5 permissioned read-only tools, policy, decision), orchestrated by a deterministic state machine.
  - Deterministic verifier that checks evidence IDs, re-computes numeric claims, validates citations and detects PII.
  - Policy engine with human-in-the-loop approval (authority matrix, four-eyes).
  - Allow-listed action execution.
- **LLMOps:** versioned YAML prompts; LLM gateway with model routing (Sonnet-class / Haiku-class), structured output via tool schemas, retries with schema repair, circuit breaker, token/cost/latency ledger; Anthropic provider, deterministic provider, fault-injection provider.
- **Security:**
  - JWT authentication, PBKDF2 password hashing, account lockout, login rate limiting.
  - RBAC with 5 roles and 11 permissions; tenant isolation.
  - Prompt-injection detection at ingestion with forced human review.
  - PII redaction, security headers, SHA-256 artifact verification, hash-chained audit log.
- **Observability:** JSON structured logs with request/workflow correlation, 15 Prometheus metrics, OpenTelemetry spans, 12-panel Grafana dashboard definition, 7 Prometheus alert rules.
- **DevOps:** multi-stage non-root Dockerfiles, Docker Compose, GitHub Actions (lint, SAST, dependency audit, secret scan, tests on SQLite and PostgreSQL, image build, Trivy), environment-gated Azure deploy workflow, Azure Bicep (Container Apps, PostgreSQL Flexible Server, Service Bus, Event Hubs, Key Vault, ACR, Blob, Monitor). *Written, not executed.*
- **Frontend:** Streamlit dashboard with 7 role-based pages; smoke-tested for 6 users across both tenants (0 failing renders).

## 2. Measured results

| Area | Result |
|---|---|
| Data quality | 771,818 rows → 759,983 loaded, 4,237 rejected with reasons, 7,598 duplicates removed in 37 s; replay loaded 0 rows |
| Alerting | 3,215 alerts; about 71% false positives in the test period (the problem being solved) |
| ML (803 held-out future alerts) | XGBoost + Platt PR-AUC 0.976 / ROC-AUC 0.980 vs logistic 0.958 / 0.961 under identical protocol; 91.7% recall with 0 false positives vs 26; 73.7% of alerts deprioritised at that operating point; customer-grouped CV ROC-AUC 0.979 ± 0.021 |
| Calibration | Brier 0.021, ECE 0.018; Platt beat isotonic on PR-AUC (0.976 vs 0.950) |
| RAG (36 labelled queries) | Hybrid + rerank Recall@5 0.903, MRR 0.801, nDCG@5 0.807 vs dense-only Recall@5 0.736 |
| Access control | 21,147 retrieved results audited, 0 unauthorised |
| Agents (160 held-out alerts, deterministic reference agents) | 100% task success; 100% schema validity; groundedness 1.0; 735 numeric claims verified, 0 mismatches; 0 dangerous actions executed without approval; 100% approval compliance; 0 suspicious alerts auto-closed |
| Adversarial | 96 fault-injection runs across 12 failure modes: 100% safe outcomes, 0 unsafe executions |
| Performance (1 CPU, uvicorn + PostgreSQL) | Alert list p95 64 ms at 161 req/s; alert detail p95 50 ms; knowledge search p95 106 ms; full investigation p95 424 ms (no network LLM); 0 failures across 1,440 requests |
| Estimated LLM cost | ~21.5k tokens and ~$0.075 per investigation (token counts estimated, prices configurable) |
| Tests | 134 passing on PostgreSQL; 129 passing on SQLite (+5 PostgreSQL-only skipped) |
| Static and dependency security | ruff, Bandit clean; pip-audit found PyJWT 2.7.0 CVEs → upgraded to 2.14.0 → clean |

## 3. Engineering judgement (true stories for interviews)

| Situation | What I did | Outcome |
|---|---|---|
| First model scored ROC-AUC 0.98 off one KYC feature | Recognised synthetic-data leakage; added stale and inflated KYC profiles | More realistic feature mix |
| Threshold swung between 70% and 15% workload savings across rebuilds | Switched to out-of-fold threshold and calibration; reported operating curve | Stable operating point |
| Baseline trained on less data than XGBoost | Gave both models the same protocol | Fair PR-AUC 0.976 vs 0.958 |
| Agent evaluation showed 1/80 suspicious alerts auto-closed | Error analysis → all misses were SME cash businesses; added branch-dispersion feature and blocked auto-closure for cash-intensive SMEs | 0 auto-closed suspicious alerts; accepted auto-apply share dropping from 39% to ~21% |
| Injection in small payments went unflagged | Moved scanning to ingestion; orchestrator checks the whole alert window | Always routed to humans |
| Load test forked the audit hash chain | Diagnosed `FOR UPDATE` race; per-tenant advisory-locked autonomous appends | Chain valid under concurrency; regression test |
| 1.6% of workflows failed randomly | Traced to UUID fragments matching the phone regex | 611/611 high-risk workflows verified |
| Legitimate policies were quarantined as injection | Severity-tiered detection with context | 13/13 documents clean, poison still caught |

## 4. Do NOT claim

- Deployment to Azure, or any cloud.
- Docker images built or run.
- Real customer or bank data, or real regulatory filings.
- Agent performance of Claude models (not measured).
- Money saved, FTE reduced, or production traffic.
- BGE embeddings or a cross-encoder reranker in use (production design only).

## 5. Example bullets (edit to your voice; keep the qualifiers)

- Built an agentic AML alert-investigation platform (FastAPI, PostgreSQL/pgvector, XGBoost, Claude-ready multi-agent workflow) on 772k synthetic transactions. A calibrated risk model reached PR-AUC 0.976 vs 0.958 for a logistic baseline on held-out future alerts and deprioritised 74% of alerts at 92% recall.
- Designed verification-first agents: a deterministic verifier re-computed 735 LLM-style numeric claims against the database and blocked hallucinated evidence, fabricated citations and PII leaks. 96/96 fault-injection runs failed safe with 0 unapproved high-risk actions.
- Implemented access-controlled hybrid RAG (pgvector + BM25 + RRF + rerank) with tenant and clearance filtering in SQL: Recall@5 0.903 and 0 unauthorised results across 21k audited retrievals.
- Found and fixed production-class defects through evaluation: an audit-log race under concurrent load (advisory locks), prompt injection bypassing detection, and vulnerable JWT dependencies (pip-audit). Test suite: 134 tests on PostgreSQL.
