"""Rules-based transaction monitoring (the incumbent approach in most banks).

Rules are intentionally tuned for recall, like real TM systems, which is why most alerts are
false positives. The ML model and agents exist to prioritise and investigate these alerts,
never to replace the regulatory control that the rules represent.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

RULES_VERSION = "rules-2026.04"


@dataclass(frozen=True)
class Rule:
    code: str
    name: str
    description: str
    condition: Callable[[dict], bool]
    evidence_keys: tuple[str, ...]


RULES: list[Rule] = [
    Rule("R01_STRUCTURING", "Possible structuring", ">=2 cash deposits between EUR7,500 and EUR9,999.99 in 30 days",
         lambda f: f["near_threshold_cash_count_30d"] >= 2, ("near_threshold_cash_count_30d", "cash_in_amount_30d")),
    Rule("R02_RAPID_MOVEMENT", "Rapid movement of funds", ">=EUR5,000 in over 7 days with >=80% moved out",
         lambda f: f["total_in_7d"] >= 5000 and f["pass_through_ratio_7d"] >= 0.8, ("total_in_7d", "total_out_7d", "pass_through_ratio_7d")),
    Rule("R03_HIGH_RISK_JURISDICTION", "High-risk jurisdiction exposure", ">=EUR1,000 with high-risk third countries in 30 days",
         lambda f: f["high_risk_country_amount_30d"] >= 1000, ("high_risk_country_amount_30d", "high_risk_country_txn_count_30d")),
    Rule("R04_VELOCITY_SPIKE", "Transaction velocity spike", "30-day count >=3x prior monthly average and >=25 transactions",
         lambda f: f["velocity_ratio_30d"] >= 3 and f["txn_count_30d"] >= 25, ("velocity_ratio_30d", "txn_count_30d")),
    Rule("R05_LARGE_CASH", "Large cash volume", "30-day cash deposits >=EUR15,000 (retail) or >=EUR60,000 (SME)",
         lambda f: f["cash_in_amount_30d"] >= (60000 if f["segment_sme"] else 15000), ("cash_in_amount_30d", "cash_deposit_count_30d")),
    Rule("R06_DORMANT_REACTIVATION", "Dormant account reactivation", ">=60 day inactivity then >=EUR5,000 inflow in 30 days",
         lambda f: f["dormancy_gap_days"] >= 60 and f["total_in_30d"] >= 5000, ("dormancy_gap_days", "total_in_30d")),
]
RULES_BY_CODE = {r.code: r for r in RULES}


def evaluate(features: dict) -> list[Rule]:
    return [r for r in RULES if r.condition(features)]
