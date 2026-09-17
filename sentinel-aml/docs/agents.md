# Agentic workflow

## 1. Why agents, and why not one agent

An investigation is multi-step. Evidence needs are different for each typology, the agent must decide what to fetch next, and policy lookup depends on findings.

One large agent would mix permissions (data access and decision authority), be hard to evaluate per step, and fail as a unit.

Splitting the work gives each agent one job, the minimum tools, a strict output schema and its own model tier. **Orchestration itself is a deterministic state machine**, not an LLM.

## 2. Agent contracts

### Triage agent

| Field | Value |
|---|---|
| Responsibility | Typology hypotheses (≤3), priority, investigation depth |
| Model | Fast tier (Claude Haiku-class), prompt `triage@1.3.0` |
| Input | Pseudonymised alert summary: rule, risk score, anomaly percentile, segment, KYC rating, PEP flag, 11 features |
| Output | `TriageOutput` (enum typologies, priority, depth standard/enhanced, rationale ≤600 characters) |
| Tools | None |
| Boundaries | Cannot recommend outcomes |
| Failure | **Degrades** to rule-based triage (low stakes), step marked `degraded` |
| Handoff | Always to investigation |
| Verification | Schema validation |

### Investigation agent

| Field | Value |
|---|---|
| Responsibility | Gather evidence and report findings |
| Model | Reasoning tier (Claude Sonnet-class), prompt `investigation@2.1.0` |
| Input | Typologies, depth, tool specification, step budget, tool-result history |
| Output per turn | `InvestigationStep` = `call_tool` (tool + arguments) or `finish` (≤12 findings) |
| Finding schema | ID, statement, direction (aggravating / mitigating / neutral), evidence refs (`TX:`, `FEATURE:`, `PROFILE:`, `ALERT:`, `CP:`), quantitative claims from an enum of verifiable metrics |
| Tools | `get_alert_features`, `get_customer_profile`, `get_transactions`, `get_counterparty_summary`, `get_prior_alerts` |
| Tool permissions | Read-only; bound to the alert's tenant and customer by code; bounded arguments (≤90 days, ≤60 rows); no names or IBANs; free text wrapped as untrusted |
| Boundaries | ≤8 tool calls; identical call twice = loop; no finding without evidence |
| Failure | Fail closed → `NEEDS_MANUAL_REVIEW` |
| Handoff | Finish with ≥1 finding |

### Policy agent

| Field | Value |
|---|---|
| Responsibility | Map findings to applicable guidance, red flags and approval requirements |
| Model | Fast tier, prompt `policy@1.2.0` |
| Input | Findings, typologies, ≤14 retrieved chunks (ACL-filtered by requester clearance) |
| Output | `PolicyOutput` (guidance with chunk IDs, red-flag matches, approval requirements, reporting consideration) |
| Tools | Retrieval is performed by code before the call; the agent cannot search freely |
| Failure | Fail closed |
| Verification | Every chunk ID must be in the retrieved set |

### Decision agent

| Field | Value |
|---|---|
| Responsibility | Recommend one action and write an STR-quality narrative |
| Model | Reasoning tier, prompt `decision@2.0.1` |
| Input | Case file: scores, threshold, customer risk, findings, policy output, verification feedback on retry |
| Output | `DecisionOutput`: action enum (`CLOSE_FALSE_POSITIVE`, `ENHANCED_MONITORING`, `ESCALATE_STR`, `RESTRICT_ACCOUNT`), confidence 0–1, summary, narrative ≤4,000 characters, key findings with evidence refs, citations, mitigating factors, residual uncertainty |
| Tools | None |
| Boundaries | Cannot execute anything; closure with aggravating findings forbidden by prompt and verifier |
| Failure | One repair attempt with verifier feedback, then manual review |

### Verifier (deterministic)

| Check | Issue code |
|---|---|
| Every evidence ref was actually returned by a tool in this workflow | `unverified_evidence_ref` |
| Numeric claims match the database (sum of cited transactions, feature values, risk score) | `quantitative_claim_mismatch` |
| Citations are in the retrieved set | `invalid_citation`, `invalid_policy_citation` |
| No IBAN, email, card, phone or PPSN in outputs (system IDs excluded) | `pii_in_output` |
| No closure when risk ≥0.5 or ≥2 aggravating findings | `close_contradicts_model`, `close_with_unresolved_aggravating_findings` |
| No escalation without aggravating evidence | `escalation_without_aggravating_evidence` |
| Injection indicators present in case data | `prompt_injection_in_case_data` (warning, forces approval) |

Output: `VerificationReport` (passed, issues, groundedness, citation validity, claims checked/failed).

### Policy engine (deterministic)

| Action | Rule |
|---|---|
| ESCALATE_STR, RESTRICT_ACCOUNT | Always MLRO approval |
| CLOSE_FALSE_POSITIVE | Auto only at Emerald, and only if: score <0.05, anomaly <0.90, not high-risk or PEP, not a cash-intensive SME, verification passed, no injection flags. Liffey never auto-closes. Otherwise investigator approval. |
| ENHANCED_MONITORING | Auto if the tenant allows, verification passed and no injection flags; otherwise investigator |

The policy version (`approval-policy-2026.05`) is recorded on every recommendation.

## 3. Model routing

| Step | Tier | Share of tokens | Why not the other tier |
|---|---|---|---|
| Triage | Fast | Small | Extraction, not reasoning |
| Investigation (several turns) | Reasoning | Largest | Planning and evidence judgement |
| Policy | Fast | Medium (context-heavy) | Citation extraction from supplied text |
| Decision | Reasoning | Medium | Synthesis; errors are costly |

Model names are configuration (`MODEL_REASONING`, `MODEL_FAST`), not code.

## 4. Structured output enforcement

- **Anthropic provider:** a single `submit` tool whose `input_schema` is the Pydantic JSON schema, with `tool_choice` pinned to it.
- **Gateway:** parse → `schema.model_validate` (`extra="forbid"`, enums, lengths, regex on evidence refs) → on failure, one repair retry including the validation error → otherwise `LLMError`.

## 5. Offline providers and fault injection

- **`DeterministicProvider`** runs rule-based reference implementations (`ai/simulators.py`) that consume the same payloads and satisfy the same schemas. They make the full workflow runnable in CI and give a decision baseline. **They are not an LLM.**
- **`FaultInjectingProvider`** simulates:
  - `timeout`, `error`
  - `malformed_json`, `schema_invalid`
  - `loop`
  - `hallucinated_evidence`, `fabricated_citation`, `pii_leak`
  - `unsafe_close`

## 6. Measured behaviour

See evaluation.md. In summary:

- **160 held-out workflows:** 100% success, groundedness 1.0, 0 unsafe executions, 0 suspicious alerts auto-closed.
- **96 fault-injection runs:** 100% safe outcomes.

**Real bugs found through agent evaluation:**

1. **Evidence/claim mismatch** (sum over more rows than cited), caught by the verifier.
2. **Auto-closure of a suspicious cash-business alert**, fixed by policy and a new feature.
3. **Injection flags depended on which rows the agent viewed**, fixed by ingestion-time scanning.
4. **UUID fragments flagged as phone numbers**, fixed in PII detection.

## 7. Running with real Claude

```bash
export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... MODEL_REASONING=claude-sonnet-5 MODEL_FAST=claude-haiku-4-5-20251001
python evaluation/agent_eval.py --n 60
```

Compare against the deterministic baseline:

1. Decision quality vs ground truth.
2. Verification failure rate (the real hallucination rate).
3. Tokens and cost.
4. p95 latency.
