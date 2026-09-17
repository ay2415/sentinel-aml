---
doc_id: emerald-sop-alert-handling
title: Emerald Digital Bank - Transaction Monitoring Alert Handling SOP
doc_type: sop
classification: internal
tenant_id: emerald
source: Emerald Digital Bank Financial Crime Compliance
version: "4.3"
---
# Emerald Digital Bank - Transaction Monitoring Alert Handling SOP

## Service levels
Critical alerts must be reviewed within 1 business day, high within 3 business days, medium within 7 and low within 14. SLA breaches are reported weekly to the Head of Financial Crime.

## Roles
Level 1 analysts triage alerts, gather evidence and draft recommendations. Level 2 investigators approve closures and enhanced monitoring decisions. The MLRO decides on external reporting and account restrictions. The four-eyes principle applies: the person who prepared a recommendation cannot approve it.

## Use of AI-assisted investigation
The AI investigation workflow may gather evidence, retrieve policy and draft recommendations and narratives. It may not close alerts, file reports or restrict accounts without the approvals set out below. Investigators must review cited evidence before approving. Any AI output that cites evidence which cannot be verified must be rejected and the alert investigated manually.

## Approval matrix
- Close as false positive: Level 2 investigator approval, except automatic closure is permitted only when the model risk score is below 0.05, the anomaly percentile is below 0.90, the customer is not high risk or PEP, the customer is not a cash-intensive business (cash below 30% of 30-day inflows), verification passed and no prompt-injection indicators were detected.
- Enhanced monitoring: may be applied automatically when verification passed; Level 2 reviews the monitoring list weekly.
- Escalate for STR: MLRO approval required.
- Account restriction: MLRO approval required; restrictions must be reviewed within 5 business days.

## Evidence standards
Every recommendation must reference specific transaction identifiers or profile facts. Statements about amounts must match system data. Narratives must not contain customer names, IBANs or contact details beyond the case reference.
