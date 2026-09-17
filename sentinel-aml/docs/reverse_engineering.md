# Reverse-engineering guide

**Goal:** understand every part of SentinelAML well enough to rebuild it, defend each decision, and say what you would change.

**Method:** run → observe → read → break → explain.

**Format:** 5 levels, then a 14-day plan sized for about 4-hour study sessions.

---

## Level 0: The business, in plain English

**What a bank must do.** Irish banks and payment firms must watch customer transactions for money laundering. When something looks suspicious, they report it to FIU Ireland (the Garda Financial Intelligence Unit) and to the Revenue Commissioners. They must not tell the customer (tipping off).

**How they watch today.** Rules: "two cash deposits just under €10,000 within 30 days", and so on. Rules fire alerts. Most alerts are innocent, for example a takeaway lodging weekend takings.

**What SentinelAML does.** Keeps the rules, then:

1. Scores each alert.
2. Lets AI agents gather evidence and read policy.
3. Checks the AI's claims against the database.
4. Makes a person approve anything serious.

### Glossary

| Term | Meaning |
|---|---|
| Alert | One rule firing for one customer at one point in time |
| Typology | A known laundering pattern (structuring, money mule, funnel account…) |
| STR | Suspicious Transaction Report to FIU Ireland and Revenue |
| MLRO | Money Laundering Reporting Officer: the person legally responsible for reporting decisions |
| KYC | Know Your Customer: the profile (occupation, expected turnover, risk rating) |
| PEP | Politically exposed person (needs enhanced due diligence) |
| False positive | Alert on innocent activity |
| Four-eyes | The person who prepared a decision cannot approve it |
| Tenant | A client organisation (Emerald Digital Bank, Liffey Payments) sharing the platform |
| Clearance | The document sensitivity level a role may read |

**Checkpoint:** explain the system to a non-technical friend in 60 seconds, without saying "AI".

---

## Level 1: Run it and watch

Do each step, then write one sentence on what you saw.

| # | Do | Observe | Where in code |
|---|---|---|---|
| 1 | `make pipeline` | JSON report: rows loaded, rejected, alerts, model gates | `backend/app/pipeline.py` |
| 2 | Open `data/raw/transactions.csv`; find `not-a-date` and a negative amount | Defects are deliberate | `data/generator.py::_inject_defects` |
| 3 | `psql`: `SELECT reason, count(*) FROM rejected_records GROUP BY 1;` | Every reject has a reason | `data/validation.py` |
| 4 | `SELECT rule_code, count(*), avg(risk_score) FROM alerts GROUP BY 1;` | Rules differ in volume and risk | `data/rules.py` |
| 5 | `make api`, open `/docs`, get a token as `alice.analyst` | JWT and roles | `api/routes/auth.py` |
| 6 | `GET /api/v1/alerts?status=NEW` | Ranked queue, pagination | `api/routes/alerts.py` |
| 7 | Start an investigation on the top alert | Workflow status `AWAITING_APPROVAL` | `agents/orchestrator.py` |
| 8 | `GET /api/v1/investigations/{id}` | Steps, tool calls, LLM calls with model and prompt version | Same |
| 9 | Approve as `ivan.investigator` (fails), then as `maeve.mlro` | Authority matrix | `services/approvals.py` |
| 10 | `GET /api/v1/audit/verify` as MLRO | Hash chain valid | `services/audit.py` |
| 11 | Knowledge search as analyst vs MLRO: "law enforcement liaison" | Different results by clearance | `rag/retriever.py` |
| 12 | `make dashboard`; log in as each role | Pages differ by role | `frontend/app.py` |

---

## Level 2: Component by component

For each component, learn the eight answers: **what, why, talks to, why this technology, alternatives, failure modes, security, scale and cost.**

### 2.1 Synthetic data generator: `data/generator.py`

| Question | Answer |
|---|---|
| What | Creates customers, accounts, transactions and labels with personas and typologies |
| Why | Real AML data is confidential; public datasets lack KYC, free text and tenants |
| Talks to | Writes CSVs + manifest; ingestion reads them; training reads `labels.csv` |
| Why this approach | Behavioural simulation encodes realistic false positives (cash businesses, property sales, family remittances) |
| Alternatives | PaySim/AMLworld (no KYC or text), GAN/CTGAN (hard to control typologies, can memorise) |
| Failure modes | Too-easy data gives inflated metrics (happened: ROC-AUC 0.98 from one feature) |
| Security | No real PII; names are random combinations |
| Scale | NumPy per customer; 772k rows in about 20 s |

**Exercise:** set `suspicious_rate=0.02`, rebuild. Predict first: what happens to PR-AUC and why?

### 2.2 Validation and ingestion: `data/validation.py`, `data/ingestion.py`

| Question | Answer |
|---|---|
| What | Schema check → row checks → clean → dedupe → injection scan → gates → load |
| Why | Bad data silently corrupts features, models and reports |
| Talks to | `accounts` (referential check), `ingestion_batches`, `rejected_records`, `transactions` |
| Why this technology | pandas vectorised checks; PostgreSQL `COPY` (fast) + `ON CONFLICT DO NOTHING` (idempotent) |
| Alternatives | Great Expectations/Pandera (richer reports, more dependencies); dbt tests (post-load); Spark (unneeded at this size) |
| Failure modes | Gate too strict blocks valid batches; too loose loads garbage; replays double count without idempotency |
| Security | Tenant/account mismatch rejected (prevents cross-tenant writes); references normalised and scanned |
| Scale | Batch → Event Hubs consumers with the same validation code; per-partition idempotency |

**Key idea:** quarantine the whole batch when gates fail. Partial loads create reconciliation nightmares.

### 2.3 Features and rules: `data/features.py`, `data/rules.py`, `data/alerting.py`

| Question | Answer |
|---|---|
| What | 33 point-in-time features; 6 rules; weekly scan with 28-day suppression |
| Why | Rules are the regulated control; features feed rules, ML and agents |
| Talks to | Reads transactions; writes alerts with a **feature snapshot** |
| Why this design | One feature function for training and serving prevents skew; `ts <= as_of` prevents leakage |
| Alternatives | Feature store (Feast/Databricks) at scale; SQL window functions (harder to test) |
| Failure modes | Undefined features for new customers (happened: velocity spikes, false drift); timestamp resolution bugs (happened: 0 alerts) |
| Scale | Streaming feature computation and an online store beyond ~millions of events/day |

**Exercise:** find where `unique_cash_branches_30d` is computed. Explain why it was added (see ml.md, section 4).

### 2.4 Database: `database/models.py`, `database/types.py`

| Question | Answer |
|---|---|
| What | 20 tables covering data, ML, RAG, workflow, approvals, audit |
| Why PostgreSQL | ACID for approvals and actions; JSONB for semi-structured outputs; pgvector keeps vectors under the same ACL and transactions |
| Alternatives | Separate vector database (second consistency and ACL surface); MongoDB (weak relational integrity for approvals) |
| Failure modes | Races (double approval, double investigation, audit fork) handled by constraints, optimistic locks, row locks, advisory locks |
| Security | Tenant columns everywhere; constraints as a last line of defence |
| Scale | Partition transactions; read replicas; HNSW index; row-level security |

**Exercise:** open two `psql` sessions and try to approve the same recommendation. Which constraint stops the second?

### 2.5 ML pipeline: `ml/train.py`, `ml/calibration.py`, `ml/registry.py`, `ml/scoring.py`, `ml/drift.py`

| Question | Answer |
|---|---|
| What | Train logistic baseline + XGBoost; calibrate; threshold; Isolation Forest; register; gate; score with lineage; drift |
| Why XGBoost | Tabular, non-linear, fast, explainable contributions |
| Why calibration | Policies use score thresholds (for example <0.05 auto-close), so scores must behave like probabilities |
| Why a temporal split | Deployment predicts the future |
| Alternatives | LightGBM, CatBoost, logistic only (the measured gap was small in ranking but large in false positives: 26 vs 0) |
| Failure modes | Leakage, unstable thresholds (happened), unfair baselines (happened), stale model, pickle tampering (SHA-256 check) |
| Scale | Batch scoring job; model server with micro-batching; shadow deployment for challengers |
| Cost | Training in seconds on CPU; inference about 1 ms |

**Exercise:** change `TARGET_RECALL` to 0.98 and retrain. Compare the deprioritised share with the operating curve in evaluation.md.

### 2.6 RAG: `rag/documents.py`, `rag/embeddings.py`, `rag/indexer.py`, `rag/retriever.py`

| Question | Answer |
|---|---|
| What | Parse, scan, chunk, embed, store; ACL-filtered hybrid search; context builder |
| Why hybrid | Measured: dense 0.736 vs BM25 0.880 vs hybrid 0.903 Recall@5. Domain terms need exact match; paraphrases need semantics. |
| Why ACL in SQL | Unauthorised chunks never become candidates |
| Alternatives | Azure AI Search, Elasticsearch/OpenSearch, Pinecone; cross-encoder reranker |
| Failure modes | Poisoned documents, stale versions, empty retrieval, embedding model changes needing re-index |
| Security | Quarantine, four-eyes approval, safe YAML, comment stripping, delimiter neutralisation |
| Scale | HNSW, `tsvector` full text, caching per KB version |

**Exercise:** upload `data/adversarial/poisoned_policy_update.md` via the API. Explain each rule it trips.

### 2.7 LLM gateway and prompts: `ai/gateway.py`, `ai/providers.py`, `ai/prompts.py`, `prompts/*.yaml`

| Question | Answer |
|---|---|
| What | Single entry point for all model calls: routing, schema enforcement, retries, circuit breaker, cost ledger |
| Why | Agents must not each reinvent reliability, and every call must be attributable (model, prompt version) |
| Why tool-schema structured output | Model must return arguments matching a JSON schema; more reliable than "reply in JSON" |
| Alternatives | LiteLLM proxy, Azure API Management, LangChain/LangGraph (more abstraction than needed; harder to control failure semantics) |
| Failure modes | Timeouts, rate limits (429/529), invalid JSON, outages, cost runaway |
| Security | Redacted output previews; the key is only in settings |
| Scale and cost | Per-tenant token budgets, prompt caching, batch processing for overnight work |

**Exercise:** write a provider that returns invalid JSON twice. Trace the `llm_calls` row it produces.

### 2.8 Agents, verifier, orchestrator: `agents/*.py`

| Question | Answer |
|---|---|
| What | Triage → investigation (tool loop) → policy → decision → verify → policy engine |
| Why multiple agents | Separation of duties, minimal tools, per-step model choice and evaluation |
| Why a deterministic orchestrator | Predictable control flow, bounded loops, testable failure semantics |
| Alternatives | LangGraph (graph orchestration), Semantic Kernel, AutoGen (free-form agent chat: hard to govern) |
| Failure modes | Hallucinated evidence, loops, unsafe recommendations, injection. Each has a fault-injection test. |
| Security | The LLM has no write path; tools are case-scoped; injection forces human review |
| Cost | ~21.5k tokens per investigation (estimated), bounded by step limits |

**Exercise:** in `ai/simulators.py`, make the decision agent recommend `RESTRICT_ACCOUNT` for every alert. Run the suite. Which controls stop it? (Policy engine: MLRO approval. Verifier: escalation without aggravating evidence.)

### 2.9 Services: `services/policy_engine.py`, `approvals.py`, `actions.py`, `audit.py`

| Question | Answer |
|---|---|
| What | Approval rules, human decisions, allow-listed execution, tamper-evident trail |
| Why | Legal accountability and auditability |
| Alternatives | OPA/Rego for policy as code; event-sourced audit to immutable storage |
| Failure modes | Audit race (happened), policy drift from SOP (the SOP document was updated with the code) |

### 2.10 API and security: `api/*`, `security/*`, `main.py`

| Question | Answer |
|---|---|
| What | 26 operations; JWT, RBAC, tenant scoping, validation, error envelope, middleware (request ID, metrics, rate limit, headers, body size) |
| Why FastAPI | Typed request validation, OpenAPI docs, dependency injection for auth |
| Alternatives | Django REST (heavier), Flask (less typing), gRPC internally |
| Failure modes | Leaking existence of other-tenant IDs (prevented by 404), stack traces (uniform 500), DB outage (503) |

### 2.11 Observability, dashboard, DevOps

**Observability**

- **What:** Prometheus metrics, JSON logs, OTel spans.
- **Why:** you can't operate LLM systems without cost and latency per prompt.
- **Alternative:** vendor APM only.

**Dashboard**

- **What:** Streamlit, API-only access.
- **Why:** backend-first team, fast to change.
- **Alternative:** React (better UX, more skills needed).

**DevOps**

- **What:** multi-stage Docker, Compose, GitHub Actions with security gates, Bicep.
- **Why:** reproducible builds, supply-chain checks, reviewable infrastructure.

---

## Level 3: Trace one alert through the code

Open the files side by side and follow a single alert.

1. `data/generator.py::_typology_txns`: a money-mule customer is created.
2. `data/ingestion.py::clean_transactions`: a payment reference is normalised and scanned (`reference_flags`).
3. `data/alerting.py::run_scan` → `features.py::compute_features` → `rules.py::evaluate`: R02 fires; the snapshot is stored.
4. `ml/scoring.py::ModelService.score_alert`: verified artifact → raw score → Platt → contributions → `predictions` row → priority.
5. `api/routes/alerts.py::start_investigation`: auth dependency `require(START_INVESTIGATION)`.
6. `agents/orchestrator.py::create_run`: tenant filter, status check, optimistic lock, audit.
7. `Orchestrator.execute` → `_window_injection_flags` → `TriageAgent.run` → `LLMGateway.structured`.
8. `InvestigationAgent.run`: loop, `ToolExecutor.run` (permission, argument validation, scope) → `disclosed_refs`.
9. `PolicyAgent.run`: `Retriever.search` with the requester's clearance → `build_context`.
10. `DecisionAgent.run` → `verifier.verify_decision` → optional repair → `policy_engine.evaluate`.
11. `Recommendation` saved `PENDING_APPROVAL (mlro)`.
12. `api/routes/recommendations.py::decide` → `services/approvals.py::decide` (authority, four-eyes) → `services/actions.py::execute` → `STRReport` → `audit.record`.

**Checkpoint:** draw this as a sequence diagram from memory, then compare with architecture.md.

---

## Level 4: Break it on purpose

Predict the outcome before running each one.

| # | Change | Expected failure | Lesson |
|---|---|---|---|
| 1 | Remove `Alert.tenant_id == p.tenant_id` in `get_alert` | `test_tenant_isolation_api` fails | Tenant filters are security code |
| 2 | Post-filter ACL instead of the SQL filter | Access-control tests may pass but results drop below K | Pre-filter vs post-filter |
| 3 | Delete the advisory lock in `audit.record` | PostgreSQL concurrency test fails intermittently | Races need concurrency tests |
| 4 | Make `DeterministicProvider` cite `TX:FAKE` | Verifier: `unverified_evidence_ref` | The LLM is untrusted |
| 5 | Remove the cash-intensive rule from the policy engine | `agent_eval`: suspicious auto-closed > 0 | Policy compensates for model blind spots |
| 6 | Train XGBoost on train only, logistic on dev | Gate comparison becomes unfair | Protocol matters |
| 7 | Remove system-ID masking in `pii.py` | Random verification failures | Nondeterministic bugs from data content |
| 8 | Set `agent_max_tool_steps=2` | Investigations fail closed | Budgets vs thoroughness |
| 9 | Break `FORCE_NOT_NULL` in COPY | Ingestion NotNull error | Empty string vs NULL in CSV |
| 10 | Replace Platt with isotonic | PR-AUC drops (ties) | Calibration choice affects ranking |

---

## Level 5: Senior-engineer view

**Questions to answer in your own words:**

1. **Where is the system's real safety boundary?** The allow-listed executor plus the policy engine plus human approval, not the prompts.
2. **What is the weakest measured component?** Dense retrieval with GloVe (0.736), and the model's blind spot on weak cash-business typologies.
3. **What's unmeasured?** Real Claude quality, real bank data, Docker and Azure deployment.
4. **What would break first at 100× load?** Synchronous investigations and in-memory BM25/rate limits. Then per-tenant audit serialisation.
5. **What would you change with a team of five and six months?**
   - Real labels from case management.
   - Alembic migrations.
   - Entra ID.
   - Service Bus workers.
   - BGE + cross-encoder reranker.
   - An LLM-judge for narrative quality validated against MLRO ratings.
   - Shadow mode against the human process before any auto-closure.
6. **Which decisions would you reverse?**
   - Autonomous audit writes (outbox pattern instead).
   - Regex injection detection as more than a signal.
7. **How do you prove value to a bank?**
   - A 3-month shadow pilot: time-to-disposition, QA pass rate, missed-suspicious rate versus the current process.
   - Decide auto-closure thresholds with Compliance using the operating curve.

---

## 14-day plan (about 4-hour sessions)

| Day | Session | Output to produce |
|---|---|---|
| 1 | Level 0 + README + architecture.md sections 1–2 | 60-second verbal explanation, recorded |
| 2 | Level 1 steps 1–6 | Notes: one sentence per step |
| 3 | Level 1 steps 7–12 | Screenshot of an investigation trace and audit verification |
| 4 | 2.1–2.2 data generator and ingestion; data_pipeline.md | Diagram of ingestion from memory; explain the 9 defects |
| 5 | 2.3 features and rules; do the exercise | Explain point-in-time correctness and the two bugs |
| 6 | 2.4 database; database.md; concurrency exercise | Explain 4 concurrency controls |
| 7 | 2.5 ML; ml.md; retrain with a new target recall | Operating-curve explanation for a compliance manager |
| 8 | Review day: interview.md basic questions 1–30 aloud | Mark weak answers |
| 9 | 2.6 RAG; rag.md; poisoning exercise | Explain hybrid + ACL + poisoning in 3 minutes |
| 10 | 2.7–2.8 gateway and agents; agents.md | Draw agent contracts from memory |
| 11 | 2.9–2.10 services, API, security; security.md | Threat-model walkthrough |
| 12 | Level 3 trace + Level 4 breaks 1–5 | Sequence diagram; results of the break experiments |
| 13 | Level 4 breaks 6–10; evaluation.md; observability.md | Explain each "finding that changed the system" |
| 14 | Level 5 + interview.md intermediate and advanced questions; resume_evidence.md | Draft 4 resume bullets in your own words |
