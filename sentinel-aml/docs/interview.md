# Interview preparation: SentinelAML

90 questions: 30 basic, 30 intermediate, 30 advanced. Each has four parts:

- **Tests:** what the interviewer is checking.
- **Strong answer:** grounded in this project where possible.
- **Follow-up:** the likely next question.
- **Mistake:** the common error to avoid.

Quote numbers only from `docs/evaluation.md`, and always say "synthetic data".

**Contents:**

- Basic: B1–B30
- Intermediate: I1–I30
- Advanced: A1–A30

---

## Basic

### B1. Describe your project in two minutes.
- **Tests:** communication; business framing before technology.
- **Strong answer:** "Banks run rules that flag suspicious transactions, and most alerts are false positives. I built SentinelAML to help investigators clear them faster without removing human accountability. It keeps the rules, then an XGBoost model ranks alerts, agents gather evidence and relevant policy, a deterministic verifier checks every claim against the database, and a policy engine forces MLRO approval for reports or restrictions. On synthetic data, the model deprioritised 74% of alerts at 92% recall on a held-out future period, and 96 fault-injection runs all failed safe."
- **Follow-up:** Why synthetic data?
- **Mistake:** Starting with the tech stack, or claiming real bank impact.

### B2. What is the difference between a list and a tuple in Python, and where did you rely on immutability?
- **Tests:** Python fundamentals.
- **Strong answer:** Lists are mutable; tuples are immutable and hashable, so they can be dictionary keys or set members. I use frozensets for the high-risk country list and frozen dataclasses for `Principal` and `Rule`, so security-relevant objects can't be changed after creation.
- **Follow-up:** Is a tuple containing a list hashable?
- **Mistake:** Saying tuples are "faster" as the main difference.

### B3. What does a Pydantic model give you in an API?
- **Tests:** Validation basics.
- **Strong answer:** Type coercion, constraints (lengths, ranges, regex, enums), clear 422 errors and OpenAPI docs. I set `extra="forbid"`, so unexpected fields such as `"admin": true` are rejected. The same models constrain LLM outputs, so a model can't invent an action like `WIRE_FUNDS`.
- **Follow-up:** Where else would you validate besides the API?
- **Mistake:** Thinking validation at the API alone is enough (I also validate at ingestion and at the database with constraints).

### B4. Explain primary key, foreign key and index.
- **Tests:** Database basics.
- **Strong answer:** A primary key uniquely identifies a row. A foreign key enforces that a referenced row exists. An index speeds lookups at write cost. In my schema, `transactions.id` is the source system's ID, which doubles as the idempotency key. `predictions.model_version_id` is a foreign key so every score traces to a model. The index `(tenant_id, customer_id, ts)` serves the 30-day window queries.
- **Follow-up:** Why put `tenant_id` first in the index?
- **Mistake:** Indexing every column "for speed".

### B5. Write SQL to count alerts by rule with the average risk score.
- **Tests:** GROUP BY and aggregates.
- **Strong answer:** `SELECT rule_code, COUNT(*) AS alerts, AVG(risk_score) AS avg_score FROM alerts WHERE tenant_id = :t GROUP BY rule_code ORDER BY alerts DESC;` Always filter by tenant, and use bound parameters.
- **Follow-up:** Add the share of alerts with score ≥ 0.5.
- **Mistake:** Forgetting the tenant filter, or string-formatting parameters into SQL.

### B6. What is a REST API and what HTTP status codes does your API use?
- **Tests:** HTTP semantics.
- **Strong answer:** Resource-oriented endpoints over HTTP verbs.

  | Code | When |
  |---|---|
  | 200 | Read |
  | 201 | Investigation created |
  | 202 | Asynchronous job queued |
  | 401 | Unauthenticated |
  | 403 | Wrong role |
  | 404 | Not found, **including other tenants' IDs** |
  | 409 | Conflicts (double approval, reused idempotency key) |
  | 413 | Payload too large |
  | 422 | Validation failure |
  | 423 | Account locked |
  | 429 | Rate limited |
  | 503 | Database down |

- **Follow-up:** Why 404 rather than 403 for another tenant's alert?
- **Mistake:** Returning 200 with an error body.

### B7. What is authentication vs authorisation?
- **Tests:** Security basics.
- **Strong answer:** Authentication proves who you are (JWT from login). Authorisation decides what you may do: role permissions, tenant scope, plus the approval authority matrix (only an MLRO approves an STR) and four-eyes (you can't approve your own investigation).
- **Follow-up:** Where is authorisation enforced: UI or API?
- **Mistake:** Relying on hiding buttons in the UI.

### B8. What is a JWT and what are its risks?
- **Tests:** Token understanding.
- **Strong answer:** A signed token carrying claims (user, tenant, role, expiry). Risks: accepting `alg=none`, weak secrets, long expiry, no revocation, library CVEs. I pin the algorithm, require claims, use a 60-minute expiry, refuse default secrets in production, and test forged `none` and wrong-key tokens. pip-audit found CVEs in PyJWT 2.7.0 and I upgraded to 2.14.0.
- **Follow-up:** How would you revoke a token?
- **Mistake:** Thinking JWTs are encrypted.

### B9. What is overfitting and how did you check for it?
- **Tests:** ML fundamentals.
- **Strong answer:** A model learning noise that doesn't generalise. I used a temporal hold-out (train on the past, test on the future), customer-grouped cross-validation (ROC-AUC 0.979 ± 0.021) so customers don't appear on both sides, and a simpler baseline for comparison.
- **Follow-up:** Why group by customer?
- **Mistake:** Only using random train/test splits on time-dependent data.

### B10. Precision vs recall. Which matters more in AML?
- **Tests:** Metric reasoning in context.
- **Strong answer:** Precision: of flagged alerts, how many are truly suspicious. Recall: of suspicious alerts, how many were caught. Missing laundering is worse, so I set the threshold for about 95% recall on development data, then measured the workload cost. On test: 91.7% recall with 73.7% of alerts deprioritised.
- **Follow-up:** Why did test recall come out lower than 95%?
- **Mistake:** Choosing accuracy on imbalanced data.

### B11. What is ROC-AUC vs PR-AUC?
- **Tests:** Imbalanced evaluation.
- **Strong answer:** ROC-AUC measures ranking across all thresholds, including many true negatives. PR-AUC focuses on the positive class, so it's more informative when positives are rare. I report both; PR-AUC 0.976 vs 0.958 for the baseline.
- **Follow-up:** When can ROC-AUC look good but PR-AUC be poor?
- **Mistake:** Treating AUC as a threshold-level metric.

### B12. What is a confusion matrix? Read yours.
- **Tests:** Interpreting results.
- **Strong answer:** Counts of true negatives, false positives, false negatives and true positives. Mine on test: 573 TN, 0 FP, 19 FN, 211 TP for XGBoost, versus 26 false positives for logistic regression. The 19 false negatives are the risk, which is why low scores don't mean auto-closure.
- **Follow-up:** What did you do about those 19?
- **Mistake:** Celebrating zero false positives without discussing false negatives.

### B13. What is RAG?
- **Tests:** GenAI basics.
- **Strong answer:** Retrieve relevant documents, then have the LLM answer using them, with citations. It keeps knowledge current and access-controlled without retraining. I retrieve policy and typology passages filtered by tenant and clearance, and the verifier rejects citations that weren't retrieved.
- **Follow-up:** Why not fine-tune instead?
- **Mistake:** Calling any chatbot "RAG".

### B14. What is an embedding?
- **Tests:** Vector representation.
- **Strong answer:** A numeric vector where similar meanings are close. Locally I use GloVe word vectors averaged with frequency weighting (300 dimensions), because model downloads were blocked. Production would use BGE (768). Dense-only retrieval reached Recall@5 0.736, which is why I added BM25.
- **Follow-up:** What happens when you change the embedding model?
- **Mistake:** Mixing vectors from different models in one index.

### B15. What is an AI agent?
- **Tests:** Agent concepts.
- **Strong answer:** An LLM that chooses actions (tool calls) in a loop toward a goal. My investigation agent decides which read-only tool to call next, up to 8 calls. It can't write data. Orchestration is a deterministic state machine.
- **Follow-up:** Why not let agents talk to each other freely?
- **Mistake:** Calling a single prompt an agent.

### B16. What is Docker and why use multi-stage builds?
- **Tests:** Containers.
- **Strong answer:** Packages an app with its dependencies. Multi-stage builds compile wheels in one stage and copy only what's needed into a slim runtime running as a non-root user, which means smaller images and a smaller attack surface. Honest note: I wrote the Dockerfiles, but Docker wasn't available in my build environment.
- **Follow-up:** How do you scan images?
- **Mistake:** Running containers as root with build tools inside.

### B17. What is CI/CD?
- **Tests:** DevOps basics.
- **Strong answer:** Automated build, test and deploy on every change. My pipeline: ruff, Bandit, pip-audit, gitleaks → tests on SQLite and PostgreSQL → image build → Trivy → manual, environment-gated Azure deploy with OIDC.
- **Follow-up:** Why a manual gate for production?
- **Mistake:** Only running unit tests.

### B18. What are environment variables used for, and what must never be in code?
- **Tests:** Configuration and secrets.
- **Strong answer:** Configuration differing per environment. Secrets (JWT secret, database password, API keys) never go in code or images. I use pydantic-settings, `.env.example` without values, startup guards that refuse defaults in production, and Key Vault in the Azure design.
- **Follow-up:** How do containers get secrets from Key Vault?
- **Mistake:** Committing `.env`.

### B19. What is logging vs metrics vs tracing?
- **Tests:** Observability basics.
- **Strong answer:** Logs are events with context, metrics are numeric time series for dashboards and alerts, traces follow one request across steps. I log JSON with request and workflow IDs, expose 15 Prometheus metrics (LLM cost, tokens, latency), and add OpenTelemetry spans per agent step.
- **Follow-up:** Why avoid putting customer IDs in metric labels?
- **Mistake:** Treating print statements as observability.

### B20. What is a unit test vs an integration test vs an end-to-end test?
- **Tests:** Testing basics.
- **Strong answer:** A unit test checks a function in isolation (PII regex, PSI). An integration test checks components together (retriever with the database). An end-to-end test runs a full business flow: my test uploads a mule pattern with an injected reference, runs monitoring, scoring and investigation, and asserts human approval is required.
- **Follow-up:** How do you test on PostgreSQL in CI?
- **Mistake:** Only testing the happy path.

### B21. What is idempotency?
- **Tests:** Reliability concept.
- **Strong answer:** Repeating an operation has the same effect as doing it once. Transaction IDs with `ON CONFLICT DO NOTHING` (replay loaded 0 rows), an `Idempotency-Key` header for starting investigations, and unique alert dedupe keys.
- **Follow-up:** What if the same key is used for a different alert?
- **Mistake:** Confusing idempotent with "safe" (read-only).

### B22. What is pagination and why cap page size?
- **Tests:** API design.
- **Strong answer:** Returning results in pages. I cap `page_size` at 100 to protect the database and memory. Offset pagination works now; keyset pagination is better for deep pages at scale.
- **Follow-up:** Why is offset slow on deep pages?
- **Mistake:** Returning all rows.

### B23. What is data validation vs data cleaning?
- **Tests:** Data engineering.
- **Strong answer:** Validation decides if a row is acceptable (reject negative amounts and future timestamps). Cleaning fixes acceptable issues (convert GBP to EUR, impute unknown country as `ZZ`, normalise text). I reject rather than guess when a fix could change meaning, such as the sign of an amount.
- **Follow-up:** When should a whole batch fail?
- **Mistake:** Silently dropping bad rows.

### B24. What is feature engineering? Give an example from your project.
- **Tests:** Applied ML.
- **Strong answer:** Turning raw data into model signals. `unique_cash_branches_30d` counts distinct branches receiving cash. I added it after error analysis showed missed cases were cash businesses; funnel accounts spread deposits across branches.
- **Follow-up:** How did you verify it helped?
- **Mistake:** Adding features without evidence.

### B25. What is class imbalance and how did you handle it?
- **Tests:** ML practice.
- **Strong answer:** One class is much rarer. I used `scale_pos_weight` in XGBoost, class-weighted logistic regression, PR-AUC, and threshold selection for recall rather than a 0.5 default.
- **Follow-up:** Why not oversample with SMOTE?
- **Mistake:** Reporting accuracy.

### B26. What is PostgreSQL JSONB and when is it the wrong choice?
- **Tests:** Data modelling judgement.
- **Strong answer:** Binary JSON with indexing support, good for semi-structured outputs (features, agent outputs, quality reports). Wrong for fields you filter or join on often, which should be columns: I keep `status`, `risk_score`, `tenant_id` and `clearance_level` as real columns.
- **Follow-up:** How would you index a JSONB key?
- **Mistake:** Storing everything as JSON.

### B27. What is the purpose of a health check, and why two?
- **Tests:** Operations.
- **Strong answer:** Liveness says the process is up (restart if not). Readiness says it can serve (database, model and knowledge base available; remove from load balancing if not). My `/health/ready` returns 503 if the database or model is unavailable.
- **Follow-up:** What happens if readiness checks call slow dependencies?
- **Mistake:** A single `/health` that always returns 200.

### B28. What is cloud cost and how would you estimate LLM cost?
- **Tests:** Cost awareness.
- **Strong answer:** Tokens in and out per model × price. I record tokens and cost per call. Estimated about 21.5k tokens and $0.075 per investigation, with prices configurable and marked as estimates. The biggest lever is not running the LLM on alerts the model deprioritises.
- **Follow-up:** How do you reduce cost without losing quality?
- **Mistake:** Ignoring input tokens, which usually dominate.

### B29. Why did you choose Python and FastAPI?
- **Tests:** Justifying choices.
- **Strong answer:** The ML and data ecosystem is Python. FastAPI gives typed validation, dependency-injected auth and automatic OpenAPI docs with little boilerplate. The trade-off: Python CPU-bound work needs processes or workers, which is fine because the heavy work is database- and LLM-bound.
- **Follow-up:** When would you use Django?
- **Mistake:** "Because it's popular."

### B30. What was the hardest bug you fixed?
- **Tests:** Debugging story.
- **Strong answer:** Under concurrent load, the audit hash chain forked. Two transactions locked the same "last" row with `SELECT … FOR UPDATE`, and PostgreSQL re-checks the locked row, not the `ORDER BY … LIMIT 1` query, so both chained to the same predecessor. I switched to a per-tenant advisory lock in a short separate transaction, and added an 8-writer regression test. The dashboard smoke test is what revealed it.
- **Follow-up:** Why not lock inside the business transaction?
- **Mistake:** Describing a bug without root cause and prevention.

---

## Intermediate

### I1. Walk me through your data pipeline and its quality gates.
- **Tests:** End-to-end data thinking.
- **Strong answer:** Read CSV → required columns → row checks (timestamps with legacy fallback, positive amounts, currency and channel enums, known account, tenant/account consistency) → clean (FX to EUR, country `ZZ`, normalise and truncate references) → dedupe (ID, near-duplicate fingerprint, already loaded) → injection scan → gates (reject ≤2%, missing country ≤5%, duplicates ≤5%) → COPY to staging → upsert. 771,818 rows → 759,983 loaded, 4,237 rejected, 7,598 duplicates, in 37 s.
- **Follow-up:** How would this run on a stream?
- **Mistake:** No lineage or reject storage.

### I2. What is point-in-time correctness and how did you guarantee it?
- **Tests:** Leakage awareness.
- **Strong answer:** Features must only use data available at prediction time. `compute_features` slices `ts <= as_of` with binary search, the same function serves training and scoring, and the snapshot is stored on the alert. Labels only count suspicious activity that started by the alert window end.
- **Follow-up:** Give an example of leakage you'd worry about in AML.
- **Mistake:** Computing features over the full history, then splitting.

### I3. Why XGBoost over logistic regression, if logistic was close on AUC?
- **Tests:** Nuanced model selection.
- **Strong answer:** Ranking metrics were close (PR-AUC 0.976 vs 0.958), but at the same ~92% recall operating point XGBoost produced 0 false positives versus 26. That's real investigator time. XGBoost also captures interactions such as SME × cash share. Logistic regression stays as the baseline and a promotion gate.
- **Follow-up:** When would you pick logistic anyway?
- **Mistake:** Choosing by AUC alone.

### I4. Why calibrate, and why Platt over isotonic?
- **Tests:** Probability reasoning.
- **Strong answer:** Policies use absolute thresholds (auto-close below 0.05), so scores must mean probabilities. Isotonic created ties that dropped PR-AUC to 0.950. Platt has two parameters, is monotonic so ranking is preserved, and is stable with limited data. Measured: Brier 0.021, ECE 0.018.
- **Follow-up:** How do you measure calibration?
- **Mistake:** Assuming XGBoost probabilities are calibrated.

### I5. How did you choose the decision threshold?
- **Tests:** Operating point design.
- **Strong answer:** First version: 95% recall on a 435-alert calibration slice. It was unstable (15% vs 70% workload saving across rebuilds). I moved to 5-fold customer-grouped out-of-fold predictions over the whole development period, fitted calibration on those, picked the threshold there, and reported an operating curve on test so compliance can choose.
- **Follow-up:** Why not choose the threshold on test?
- **Mistake:** Tuning on test data.

### I6. How do you monitor drift?
- **Tests:** MLOps.
- **Strong answer:** PSI per feature and on scores against a reference profile saved with the model (<0.10 stable, >0.25 significant), exposed at `/ops/drift`. I found a false alarm (PSI 0.64 on velocity) caused by a feature defined differently for customers without history, and fixed the feature rather than the threshold.
- **Follow-up:** What's the difference between data drift and concept drift?
- **Mistake:** Retraining automatically on any drift signal.

### I7. How does your model registry answer "which model produced this prediction"?
- **Tests:** Lineage.
- **Strong answer:** Every `predictions` row has `model_version_id`. `/ops/predictions/{id}/lineage` returns the version, algorithm, artifact SHA-256, training-data hash, parameters and features. Artifacts are verified before unpickling.
- **Follow-up:** How do you roll back a model?
- **Mistake:** Keeping only "latest.pkl".

### I8. Explain your hybrid retrieval.
- **Tests:** RAG depth.
- **Strong answer:** A SQL ACL filter, then dense pgvector cosine top-30 and BM25 top-30 over the permitted corpus, fused with reciprocal rank fusion (k=60), then a lightweight coverage reranker. Recall@5: dense 0.736, BM25 0.880, hybrid 0.903; reranking lifted MRR from 0.766 to 0.801.
- **Follow-up:** Why RRF instead of adding scores?
- **Mistake:** Only using vector search.

### I9. How did you evaluate retrieval?
- **Tests:** Evaluation rigour.
- **Strong answer:** 36 labelled queries, paraphrased rather than copied, with relevant chunk IDs; labels the user can't access are removed per query. Recall@5, Precision@5, MRR, nDCG@5, Hit@5 per mode, plus an ACL audit (21,147 results, 0 unauthorised). Caveat: single annotator, small corpus.
- **Follow-up:** Why is Precision@5 low (0.24)?
- **Mistake:** Evaluating RAG only by reading answers.

### I10. How do you enforce document access control in RAG?
- **Tests:** Security in RAG.
- **Strong answer:** Chunks carry `tenant_id`, `clearance_level` and `is_active` columns. The retrieval query filters on them before similarity ranking, and roles map to clearances. The policy agent retrieves with the requesting user's clearance, so an analyst's investigation can't cite restricted material.
- **Follow-up:** Why is post-filtering risky?
- **Mistake:** Filtering in the prompt ("don't use restricted documents").

### I11. What is prompt injection and how do you defend against it?
- **Tests:** LLM security.
- **Strong answer:** Untrusted text that tries to redirect the model. Layers:
  1. Detection at ingestion and on tool view.
  2. Spotlighting with `<untrusted_data>` tags plus a system instruction.
  3. Containment: schema-constrained outputs, no write tools, a verifier, and a policy engine that forces human approval when case data is flagged.

  Detection is regex, so containment is the real control.
- **Follow-up:** What bug did you find here?
- **Mistake:** "We tell the model to ignore instructions."

### I12. How do you get reliable structured output from an LLM?
- **Tests:** LLM engineering.
- **Strong answer:** A tool whose `input_schema` is the Pydantic JSON schema, with forced `tool_choice`. Then strict validation (`extra=forbid`, enums, lengths, regex), one repair retry with the validation error, then fail closed. Offline runs: 100% validity. With real Claude this is the metric to watch.
- **Follow-up:** Why only one retry?
- **Mistake:** Parsing free text with regex.

### I13. Describe your agents and why each exists.
- **Tests:** Agent design.
- **Strong answer:**

  | Agent | Model | Job | Tools | Failure |
  |---|---|---|---|---|
  | Triage | Haiku | Typology, priority, depth | None | Degrades to rules |
  | Investigation | Sonnet | Evidence | 5 read-only tools | Fails closed |
  | Policy | Haiku | Citations from retrieved text | Retrieval done by code | Fails closed |
  | Decision | Sonnet | Action + narrative | None | Fails closed |
  | Verifier | Code | Checks claims | — | — |
  | Policy engine | Code | Approval | — | — |

- **Follow-up:** Why is the orchestrator not an LLM?
- **Mistake:** Splitting agents for the diagram rather than permissions and evaluation.

### I14. What does your verifier check?
- **Tests:** Grounding.
- **Strong answer:** Evidence IDs were actually returned by tools in this workflow; numeric claims recomputed from the database (735 checked, 0 failed); citations were in the retrieved set; no PII; closure isn't contradicted by risk score or aggravating findings; escalation has aggravating evidence; injection flags. Critical issues block, with one repair attempt.
- **Follow-up:** Why deterministic instead of an LLM judge?
- **Mistake:** "The model cites sources, so it's grounded."

### I15. How do you prevent agent loops?
- **Tests:** Bounded autonomy.
- **Strong answer:** Maximum 8 tool calls, a hard ceiling on LLM turns, and an identical repeated call (tool + sorted arguments) raises `AgentLoopError`. The workflow then fails closed. Tested with an injected loop fault: 8/8 safe.
- **Follow-up:** What if the loop varies arguments slightly?
- **Mistake:** Relying only on a timeout.

### I16. How does human-in-the-loop work in your system?
- **Tests:** Governance design.
- **Strong answer:** The policy engine decides whether approval is required and which role. STRs and restrictions always need the MLRO. The approval API enforces role authority, four-eyes (initiator ≠ approver), a rationale of at least 10 characters, and refuses approving a recommendation that failed verification. Only then does the allow-listed executor act; everything is audited.
- **Follow-up:** What can be auto-applied and why?
- **Mistake:** A generic "human reviews output" without authority rules.

### I17. How did you implement multi-tenancy, and what are the risks?
- **Tests:** SaaS architecture.
- **Strong answer:** A shared schema with `tenant_id` on every table; the token's tenant filters every query; other-tenant IDs return 404; ingestion rejects mismatched rows; RAG has global plus tenant documents. Risk: one missing filter leaks data. Mitigations: tests, and PostgreSQL row-level security as the next step.
- **Follow-up:** When would you use a database per tenant?
- **Mistake:** Trusting a tenant ID sent in the request body.

### I18. How do you handle concurrency on approvals and investigations?
- **Tests:** Transactions.
- **Strong answer:** An optimistic lock (`lock_version`) on alerts stops double starts. A row lock plus unique `approvals.recommendation_id` stops double approval (second attempt → 409). `SKIP LOCKED` lets workers share a queue. Advisory locks serialise audit appends.
- **Follow-up:** Optimistic vs pessimistic: when is each appropriate?
- **Mistake:** Check-then-update without a lock or constraint.

### I19. How do you make the async worker safe?
- **Tests:** Queue processing.
- **Strong answer:** `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` so each worker claims a different run. Commit on success, roll back on exception, SIGTERM handling for graceful shutdown. Tested with 3 concurrent workers: each workflow triaged exactly once. Production: Service Bus peek-lock, max delivery count, dead-letter queue, KEDA.
- **Follow-up:** How do you handle a worker crash mid-workflow?
- **Mistake:** Polling without locking.

### I20. How do you test LLM failure modes without an LLM?
- **Tests:** Test design.
- **Strong answer:** A `FaultInjectingProvider` wraps the deterministic provider with 12 modes (timeout, error, malformed JSON, schema-invalid, loop, hallucinated evidence, fabricated citation, PII leak, unsafe close, triage error). The evaluation asserts the expected safe outcome: 96/96 safe.
- **Follow-up:** What does that not tell you?
- **Mistake:** Only mocking success responses.

### I21. What is your LLMOps ledger and why does it matter?
- **Tests:** LLMOps.
- **Strong answer:** Every call stores model, prompt name, version and hash, tokens (flagged if estimated), cost, latency, attempts, status, schema validity, retrieved chunk IDs and a redacted output preview. It answers "why did the model say this", "what did it cost", and "did the new prompt version increase failures".
- **Follow-up:** How do you version prompts?
- **Mistake:** Prompts hard-coded in Python strings.

### I22. How does model routing reduce cost?
- **Tests:** Cost engineering.
- **Strong answer:** Haiku-class for short extraction (triage, policy citations); Sonnet-class for planning and synthesis (investigation, decision). Upstream, the ML model means low-risk alerts need no LLM run at all. Next steps: prompt caching for static system prompts and batch processing for overnight queues.
- **Follow-up:** How would you prove Haiku is good enough for policy?
- **Mistake:** Using the largest model everywhere, or downgrading without evaluation.

### I23. Explain PII handling end to end.
- **Tests:** Privacy engineering.
- **Strong answer:** Agents never receive names or IBANs, only pseudonymous IDs and profile attributes. Regex redaction runs in logs, LLM output previews and tool references. The API masks names without `pii:view`, and the verifier blocks PII in narratives. I fixed a false positive where UUID fragments looked like phone numbers.
- **Follow-up:** What would you use instead of regex in production?
- **Mistake:** Redacting only in the UI.

### I24. What did your load test show and what are its limits?
- **Tests:** Performance thinking.
- **Strong answer:** A real uvicorn server with PostgreSQL on one CPU, 0 failures:

  | Endpoint | p95 latency | Throughput |
  |---|---|---|
  | Alert list | 64 ms | 161 req/s |
  | Knowledge search | 106 ms | 97 req/s |
  | Investigation (no LLM network time) | 424 ms | — |

  Limits: a shared sandbox CPU, a single worker, and no real LLM latency, which would dominate.
- **Follow-up:** How would you find the bottleneck?
- **Mistake:** Quoting throughput without environment details.

### I25. What is in your CI pipeline and why each step?
- **Tests:** DevSecOps.
- **Strong answer:**

  | Step | Catches |
  |---|---|
  | ruff | Style and security lints |
  | Bandit | Python SAST |
  | pip-audit | Dependency CVEs (it caught PyJWT) |
  | gitleaks | Secrets |
  | pytest on SQLite and PostgreSQL | Portability and pgvector behaviour |
  | Image build + Trivy | OS and library CVEs in images |

  Deploy is manual with environment approval and OIDC.
- **Follow-up:** Why test on two databases?
- **Mistake:** Treating CI as "runs tests" only.

### I26. Which Azure services did you choose and why?
- **Tests:** Cloud design justification.
- **Strong answer:**

  | Service | Why |
  |---|---|
  | Container Apps | API and workers without cluster operations |
  | KEDA | Scale workers on queue depth |
  | PostgreSQL Flexible Server + pgvector | One store for data and vectors under the same access control |
  | Service Bus | Per-message work queue with dead letters |
  | Event Hubs | High-throughput transaction stream |
  | Key Vault | Secrets via managed identity |
  | ACR | Private images |
  | Blob | Artifacts and immutable audit exports |
  | Azure Monitor | Logs, metrics, traces |

  Written in Bicep, not deployed.
- **Follow-up:** Why not AKS?
- **Mistake:** Listing services without the problem each solves.

### I27. How do you handle secrets in Azure?
- **Tests:** Cloud security.
- **Strong answer:** Key Vault with RBAC and purge protection. Container Apps reference secrets through system-assigned managed identity, so there are no secrets in images or pipelines. GitHub deploys with OIDC federated credentials. The app refuses to start in production with default secrets.
- **Follow-up:** How do you rotate the JWT secret?
- **Mistake:** Storing secrets as plain pipeline variables.

### I28. What would a Grafana dashboard for this system show, and which alerts page someone?
- **Tests:** Operability.
- **Strong answer:** Requests and p95 by route, LLM calls, latency, tokens and cost, agent step latency, tool call status, workflow outcomes, risk score quantiles, retrieval latency, injection detections. Paging alerts: API 5xx >2% and LLM failure rate >5% (investigations fall back to humans). Ticket alerts: spend spikes, manual-review share, injection surges, score shift.
- **Follow-up:** Why is an LLM outage not total downtime here?
- **Mistake:** Alerting on everything.

### I29. How did you prevent the scanner from quarantining real policies?
- **Tests:** Handling false positives in security controls.
- **Strong answer:** The first heuristic quarantined 2 real SOPs containing "Do not close an alert as a false positive". I added severity tiers: high-severity rules (override instructions, role hijack, AI directives, tag injection, exfiltration) quarantine documents; decision-steering rules apply only to customer free text. A regression test covers all 13 documents, and the poisoned fixture still trips 5 high-severity rules.
- **Follow-up:** How would you measure the scanner's precision and recall?
- **Mistake:** Maximising detection with no false-positive budget.

### I30. What did agent evaluation reveal that unit tests didn't?
- **Tests:** Evaluation value.
- **Strong answer:** A truly suspicious alert was auto-closed (1 of 80). Error analysis: all 12 low-scoring suspicious alerts were SME cash businesses. I added a branch-dispersion feature and a policy rule blocking auto-closure for cash-intensive SMEs. Result: 0 auto-closed suspicious alerts, and I accepted the automation share falling from 39% to about 21%.
- **Follow-up:** Isn't that giving up automation?
- **Mistake:** Hiding the trade-off.

---

## Advanced

### A1. Design this system for a bank with 50 million transactions a day.
- **Tests:** System design at scale.
- **Strong answer:**
  1. Event Hubs (Kafka API) partitioned by account, feeding stateless ingestion consumers that reuse the validation code and upsert idempotently.
  2. Streaming features (Flink or Spark Structured Streaming) into an online feature store; rules evaluated on the stream.
  3. Alerts land in partitioned PostgreSQL or Citus.
  4. Scoring through a batching model service.
  5. Investigations enqueued on Service Bus; workers autoscale on queue depth; per-tenant LLM token budgets through a gateway.
  6. Vector search in a dedicated HNSW index with ACL filters.
  7. Analytics on a lakehouse, not OLTP.
- **Follow-up:** Where does ordering matter and how do you keep it?
- **Mistake:** "Just add Kubernetes."

### A2. Exactly-once processing: is it achievable here?
- **Tests:** Distributed-systems realism.
- **Strong answer:** End-to-end exactly-once delivery isn't realistic; aim for at-least-once delivery with idempotent effects. Natural keys plus `ON CONFLICT DO NOTHING` for transactions, alert dedupe keys, idempotency keys on workflows, a unique recommendation per workflow, and a unique approval per recommendation. Side effects outside the database (STR submission) need an outbox with deduplication.
- **Follow-up:** How does the transactional outbox work?
- **Mistake:** Claiming Kafka gives exactly-once end to end.

### A3. Your audit log uses autonomous transactions. Defend or criticise that.
- **Tests:** Trade-off honesty.
- **Strong answer:**
  - **For:** writing the hash chain inside long business transactions serialises investigations for the duration of LLM calls; a short advisory-locked append keeps a valid chain under concurrency (tested).
  - **Against:** events persist when the business transaction rolls back, so the trail records attempts, not only outcomes.
  - **Better long term:** a transactional outbox inside the business transaction, a single sequencer that hashes committed events in commit order, and export to immutable storage.
- **Follow-up:** How do you order events by commit time?
- **Mistake:** Not knowing the downside of your own design.

### A4. How would you evaluate the agents with real Claude models?
- **Tests:** LLM evaluation design.
- **Strong answer:**
  1. Run `agent_eval.py` with `LLM_PROVIDER=anthropic` on a stratified set.
  2. Track decision agreement with ground truth, verification failure rate (the real hallucination signal), schema repair rate, tokens, cost and p95 latency, and compare with the deterministic baseline.
  3. Add narrative quality scored by an LLM judge whose rubric is calibrated against MLRO ratings on a sample (agreement measured with Cohen's kappa).
  4. Run the adversarial suite with real injection strings.
  5. Repeat runs to measure variance.
- **Follow-up:** How many samples do you need to detect a 5-point difference?
- **Mistake:** Vibe-checking a few outputs.

### A5. Why is your agent evaluation not proof of reasoning quality?
- **Tests:** Scientific honesty.
- **Strong answer:** The agents were deterministic rule-based stand-ins, so decision quality reflects my heuristics, and schema validity is trivially 100%. What it does prove: the orchestration, verifier, policy engine and approval controls behave correctly across 160 normal and 96 adversarial workflows. Two genuine bugs were found this way.
- **Follow-up:** Then why include it?
- **Mistake:** Presenting 100% validity as an LLM result.

### A6. Synthetic data made the model look excellent. How do you convince a bank it will work?
- **Tests:** Validation strategy.
- **Strong answer:**
  1. Retrain on the bank's historical dispositions (noisy, delayed labels).
  2. Temporal back-test.
  3. Shadow mode for 3 months: score and investigate in parallel without affecting decisions.
  4. Compare missed-suspicious rate, time to disposition and QA pass rate against the current process.
  5. Agree the operating point with Compliance using the operating curve.
  6. Model risk management review (documentation, challenger models).
- **Follow-up:** How do you handle label bias from past investigator behaviour?
- **Mistake:** Extrapolating synthetic metrics.

### A7. Labels come from past investigations, which were biased by the old rules. Consequences?
- **Tests:** Selection bias.
- **Strong answer:** We only observe outcomes for alerted cases, so the model learns "suspicious given the rules fired" and can't discover patterns the rules never flagged. Mitigations: keep Isolation Forest as a novelty signal (test ROC-AUC 0.940), random sampling of non-alerted customers for review, SAR feedback from law enforcement, and periodic below-the-line testing of rule thresholds.
- **Follow-up:** What is below-the-line testing?
- **Mistake:** Assuming labels are ground truth.

### A8. How would you detect a new laundering typology the model has never seen?
- **Tests:** Novelty detection.
- **Strong answer:** Unsupervised anomaly percentile (Isolation Forest) with a policy that anomaly ≥0.90 blocks auto-closure; clustering of high-anomaly, low-score cases for analyst review; network features (shared counterparties across customers); drift on the score distribution; and a feedback loop where confirmed new patterns become labelled typologies and rules.
- **Follow-up:** How do you avoid drowning analysts in anomalies?
- **Mistake:** "Retrain the classifier."

### A9. How would you add graph analytics for mule networks?
- **Tests:** Advanced data modelling.
- **Strong answer:** Build a customer–counterparty graph from transactions (a graph database, or PostgreSQL edge tables for moderate size). Compute features: shared counterparties, fan-in/fan-out, community detection, path length to known mule accounts. Add them as model features and as an agent tool (`get_network_neighbourhood`, read-only and scoped). Watch for privacy: exposing other customers' data inside a case needs access rules.
- **Follow-up:** How do you keep graph features point-in-time correct?
- **Mistake:** Building a graph database without a clear feature or decision use.

### A10. How do you secure tools when an agent can call them?
- **Tests:** Agent security architecture.
- **Strong answer:**
  - Per-agent allow-list; tools bound in code to (tenant, alert, customer), never taken from model arguments.
  - Pydantic-bounded arguments with `extra=forbid` (an attempt to pass `customer_id` was rejected in tests).
  - Read-only; row limits; PII removed; free text wrapped as untrusted; every call logged, including denied ones.
  - Evidence IDs recorded for the verifier.
  - Write actions exist only in the executor after policy and approval.
- **Follow-up:** What changes if a tool must write (for example request documents from a customer)?
- **Mistake:** Giving the agent a SQL tool.

### A11. What if the LLM provider is down for an hour?
- **Tests:** Resilience.
- **Strong answer:** The circuit breaker opens per model after repeated failures, so calls fail fast. Workflows fail closed to manual review (fault-tested). Alerts stay ranked by the ML model, which has no LLM dependency. The paging alert fires on LLM failure rate. In async mode, messages stay on the queue and retry later. Optional: a fallback model behind the gateway, evaluated beforehand.
- **Follow-up:** Would you silently switch to another provider?
- **Mistake:** Degrading to unverified heuristics without telling users.

### A12. How would you run a new prompt version safely?
- **Tests:** LLMOps release process.
- **Strong answer:**
  1. The prompt YAML change goes through pull request review.
  2. Offline evaluation on the fixed dataset and the adversarial suite, compared with the current version.
  3. Canary to a small traffic share with prompt version in the ledger.
  4. Compare verification failure rate, repair rate, tokens and decision distribution.
  5. Roll back by reverting the file.

  Past decisions stay attributable through the recorded prompt hash.
- **Follow-up:** How do you prevent a prompt change from shifting decisions silently?
- **Mistake:** Editing prompts in production.

### A13. Tenant isolation: application filters vs row-level security vs separate databases.
- **Tests:** Isolation depth.
- **Strong answer:**

  | Option | Pros | Cons |
  |---|---|---|
  | Application filters (current) | Simple, portable, tested | One missed filter leaks data |
  | PostgreSQL row-level security with `SET app.tenant` per transaction | Database enforces isolation even with a bug | Complicates pooling and superuser access |
  | Database per tenant | Strongest isolation, per-tenant keys and residency | Operational overhead, cross-tenant analytics hard |

  I'd add RLS next, and offer a dedicated database to large regulated clients.
- **Follow-up:** How does connection pooling interact with RLS?
- **Mistake:** Treating isolation as one-size-fits-all.

### A14. How would you make retrieval scale to a million chunks with access control?
- **Tests:** Vector search internals.
- **Strong answer:** An HNSW index on pgvector (or a dedicated engine) with ACL attributes. Pure post-filtering of ANN results loses recall under selective filters, so use filtered ANN (pre-filter or iterative scan), partition by tenant, and keep clearance as a numeric filter. Replace in-memory BM25 with `tsvector` GIN or a search engine, and cache by (query, tenant, clearance, KB version). Re-measure Recall@K against exact search.
- **Follow-up:** What HNSW parameters trade recall for latency?
- **Mistake:** Assuming the ANN index respects metadata filters for free.

### A15. Your PII detector is regex. How would you evaluate and replace it?
- **Tests:** Security control engineering.
- **Strong answer:** Build a labelled set of narratives and references with realistic Irish formats (IBAN, PPSN, Eircode, phones) and adversarial negatives (UUIDs, transaction IDs, amounts). Measure precision and recall per entity. Compare regex, Microsoft Presidio and Azure AI Language PII. Keep regex as a fast pre-filter and remove the root risk by not sending personal data to the model.
- **Follow-up:** What false positive did you already find?
- **Mistake:** Only measuring recall.

### A16. How would you design SLOs for this platform?
- **Tests:** Reliability engineering.
- **Strong answer:**

  | SLO | Target |
  |---|---|
  | API availability | 99.9% monthly |
  | Alert list p95 | < 300 ms |
  | Asynchronous investigation completion p95 | < 5 minutes |
  | LLM-dependent investigations completing without manual fallback | ≥ 95% |
  | Scoring availability | 99.95% |

  Error budgets guide release pace. Business SLAs (critical alert reviewed within 1 business day) are tracked separately in analytics.
- **Follow-up:** How do SLOs change alerting?
- **Mistake:** SLOs on CPU usage.

### A17. Model risk management: what documentation would a regulator expect?
- **Tests:** Governance maturity.
- **Strong answer:**
  - Purpose and limitations.
  - Data lineage and label definition.
  - Validation protocol (temporal, grouped CV), metrics and operating-point rationale.
  - Explainability (contributions) and calibration.
  - Challenger comparison (logistic baseline).
  - Monitoring (drift, performance), change control (gates, versioning), human oversight design, known weaknesses (cash-business blind spot) and compensating controls.

  Most of this exists in `ml.md` plus the registry metadata.
- **Follow-up:** Who approves a model change?
- **Mistake:** Treating models as code-only artefacts.

### A18. How would you handle the EU AI Act and GDPR considerations?
- **Tests:** Regulatory awareness (not legal advice).
- **Strong answer:**
  - Assess classification with Legal. Anti-money-laundering detection is a legal obligation, but profiling individuals still requires transparency, human oversight, logging and robustness — all of which the design supports.
  - Under GDPR: a lawful basis (legal obligation), data minimisation to the LLM, processor agreements, EU data residency, retention limits balanced against AML record keeping, and DPIA documentation.
- **Follow-up:** How does tipping off interact with subject access requests?
- **Mistake:** Giving definitive legal conclusions.

### A19. Where would caching help and where is it dangerous?
- **Tests:** Caching judgement.
- **Strong answer:**
  - **Safe:** retrieval results keyed by (query, tenant, clearance, KB version); KPI aggregates for seconds; provider prompt caching of static system prompts.
  - **Dangerous:** caching decisions or tool results across cases or tenants (leakage, stale evidence); caching authorisation decisions beyond token lifetime.
  - Include tenant and clearance in every cache key.
- **Follow-up:** How do you invalidate on document approval?
- **Mistake:** A global cache keyed only by query text.

### A20. How would you reduce LLM cost by 50% without losing quality?
- **Tests:** Cost optimisation with evidence.
- **Strong answer:**
  1. Measure a token breakdown per agent from the ledger.
  2. Don't investigate alerts below the model threshold unless a human asks (largest lever).
  3. Prompt-cache static prefixes.
  4. Compress tool results into aggregates.
  5. Test Haiku for more steps, gated on verification failure rate and decision agreement.
  6. Batch non-urgent overnight work.
  7. Cap tool steps by depth.

  Each change is accepted only if offline evaluation metrics hold.
- **Follow-up:** What metric would stop a downgrade?
- **Mistake:** Downgrading models without measuring.

### A21. What happens if an attacker controls many customers' payment references?
- **Tests:** Adversarial systems thinking.
- **Strong answer:** Individual injections force human review. But a flood could overload reviewers or train staff to ignore flags. Controls: rate metric and `PromptInjectionSurge` alert; treat instruction-like references as an aggravating typology signal; group by counterparty to detect coordination; keep containment (no write path).
- **Follow-up:** Could the attacker poison the model's training labels?
- **Mistake:** Only thinking about one malicious string.

### A22. Explain optimistic locking and a failure scenario it doesn't cover.
- **Tests:** Concurrency depth.
- **Strong answer:** Each update checks `lock_version` and increments it; a stale writer gets `StaleDataError` (409). It doesn't cover invariants spanning rows or tables (for example "only one open recommendation per customer"), non-database side effects, or read skew in reports. Those need constraints, serialisable transactions or locks.
- **Follow-up:** When would you use SERIALIZABLE isolation?
- **Mistake:** Believing optimistic locking prevents all races.

### A23. How would you migrate from `create_all` to managed migrations with zero downtime?
- **Tests:** Database operations.
- **Strong answer:**
  1. Baseline Alembic from the current schema.
  2. Use expand-and-contract: add nullable columns or new tables, deploy code writing both, backfill in batches, switch reads, then drop the old column in a later release.
  3. Create indexes concurrently.
  4. Test migrations on a production-size copy in CI.
- **Follow-up:** How did adding `reference_flags` break things for you?
- **Mistake:** Running blocking `ALTER TABLE` on large tables at peak.

### A24. Would you use LangGraph or another agent framework instead of your orchestrator?
- **Tests:** Framework judgement.
- **Strong answer:** For this workflow, a fixed sequence with one bounded loop and explicit failure semantics fits in about 200 lines, is easy to test and has no hidden retries. LangGraph adds value with branching graphs, checkpointed long-running state, human-interrupt nodes and resumability. I'd adopt it if workflows became dynamic, keeping the verifier, policy engine and executor outside the framework.
- **Follow-up:** How would you resume a workflow after a crash?
- **Mistake:** Framework-first design.

### A25. How would you test for fairness or disparate impact?
- **Tests:** Responsible AI.
- **Strong answer:** Protected attributes aren't in the model, but proxies (nationality through remittance countries, occupation, segment) can create disparities. Measure alert rates, score distributions and false-positive rates across segments and countries of counterparties. I already report per-tenant and per-rule recall; the diaspora family-support persona was designed to test high-risk-country false positives. Remediate through features and policy review, not by hiding attributes.
- **Follow-up:** What conflict exists between high-risk country rules and fairness?
- **Mistake:** "We don't use sensitive attributes, so it's fair."

### A26. How would you build a feedback loop from investigators?
- **Tests:** Product and ML loop.
- **Strong answer:** Capture the approve/reject decision, rationale, final disposition and which findings were wrong. Store these as labels with timestamps. Monthly retrain with label-delay handling. Track agreement between recommendations and human decisions per prompt version. Rejected recommendations get sampled for error analysis. Guard against automation bias by blind-reviewing a sample without AI recommendations.
- **Follow-up:** What is automation bias?
- **Mistake:** Training on approvals as if they were ground truth.

### A27. What is your threat model for the model artefact supply chain?
- **Tests:** ML security.
- **Strong answer:** Risks: a malicious pickle executing code on load, tampered artefacts, dependency CVEs, training-data poisoning. Controls: SHA-256 verification against the registry before load (tested), private storage, pinned dependencies with pip-audit, image scanning, and training-data hashes. Further: signed artefacts (Sigstore), safer formats (XGBoost JSON/UBJ instead of pickle), and isolated training environments.
- **Follow-up:** Why not just use XGBoost's native format?
- **Mistake:** Treating model files as harmless data.

### A28. How would you handle 5-year AML record retention while controlling cost?
- **Tests:** Data lifecycle.
- **Strong answer:** Monthly partitions of transactions and alerts; move closed partitions to cheaper storage (Parquet on Blob with lifecycle tiers, queryable by a lakehouse engine). Keep case files and audit logs in immutable WORM Blob containers; delete after the retention period with an audited job, respecting legal holds. Keep embeddings and LLM previews shorter unless they're part of the case record.
- **Follow-up:** How does GDPR erasure interact with AML retention?
- **Mistake:** Keeping everything forever in OLTP.

### A29. If you had to cut the scope in half, what would you keep?
- **Tests:** Prioritisation.
- **Strong answer:** Keep:
  1. Ingestion with quality gates.
  2. The ML ranking with calibration and lineage.
  3. The investigation evidence pack.
  4. The verifier, policy engine and approvals.
  5. Audit.

  Cut: multi-agent split (a single evidence-gathering agent plus a decision prompt), dashboard polish, Bicep, and the second tenant. The safety boundary and measurable value survive; the architectural extras don't.
- **Follow-up:** What's the minimum to run a pilot?
- **Mistake:** Cutting tests or the verifier.

### A30. What would you do differently if starting again?
- **Tests:** Reflection and growth.
- **Strong answer:**
  1. Build the evaluation harness and concurrency tests before the features; several bugs (audit race, injection bypass) appeared late.
  2. Start with Alembic migrations.
  3. Use a transactional outbox for audit from the beginning.
  4. Store XGBoost in its native format rather than pickle.
  5. Design the synthetic data with harder, overlapping typologies earlier.
  6. Get real-LLM evaluation runs as soon as possible.
- **Follow-up:** Which of these would you fix first on a real team?
- **Mistake:** "Nothing, it went well."
