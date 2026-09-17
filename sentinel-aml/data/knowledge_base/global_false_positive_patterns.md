---
doc_id: global-false-positive-guide
title: Investigator Guide - Common False Positive Patterns and Closure Standards
doc_type: guidance
classification: internal
tenant_id:
source: Internal quality assurance findings (synthetic)
version: "2.0"
---
# Investigator Guide - Common False Positive Patterns and Closure Standards

## Why this guide exists
Rules-based monitoring is tuned for high recall, so most alerts do not represent suspicious activity. Poorly evidenced closures are the most frequent quality assurance finding. A closure must explain why the activity is consistent with the customer profile, not merely state that it is.

## Frequent false positive patterns
- Cash-intensive businesses lodging takings: consistent home branch, trading-cycle amounts, supplier and payroll outflows funded by takings.
- Property transactions: large inflow from a solicitor client account followed by an outflow to a solicitor for a purchase, usually within days.
- Salary and rent cycles that coincide inside a 7-day window.
- Regular modest remittances to family abroad, including to countries on high-risk lists, consistent with declared income.
- SME service companies receiving client invoices and paying payroll in the same week.

## Minimum evidence for closure as false positive
1. The specific rule trigger has been reviewed and explained.
2. The activity has been compared with the customer's expected turnover and history.
3. Counterparties and destinations have been reviewed for risk indicators.
4. No unresolved red flags remain from the relevant typology guide.
5. The rationale references the evidence reviewed (transaction identifiers or documents).

## When not to close
Do not close an alert as a false positive when the model risk score is high, when the customer is rated high risk or is a politically exposed person without documented enhanced due diligence, when free-text references contain instructions or attempts to influence the review, or when evidence is incomplete. Such cases require investigator review.

## Enhanced monitoring as an outcome
Where suspicion is not formed but residual risk remains, place the customer under enhanced monitoring for 90 days and schedule a periodic review. Enhanced monitoring is reversible and does not involve contacting the customer.
