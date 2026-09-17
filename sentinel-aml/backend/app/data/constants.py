"""Shared domain constants (single source of truth for generator, rules, features, validation)."""
from datetime import UTC, datetime

# ILLUSTRATIVE list only — production must sync with the current EU Delegated Regulation
# on high-risk third countries and FATF public statements (reference data service).
HIGH_RISK_COUNTRIES = frozenset({"KP", "IR", "MM", "SY", "YE"})

CHANNELS = ("card", "sepa", "instant_p2p", "cash_deposit", "cash_withdrawal", "intl_wire", "crypto_exchange", "direct_debit")
VALID_CURRENCIES = {"EUR": 1.0, "GBP": 1.17, "USD": 0.92}  # fixed reference FX rates for reproducibility
TENANTS = {"emerald": "Emerald Digital Bank", "liffey": "Liffey Payments"}

NEAR_THRESHOLD_LOW = 7_500.0
NEAR_THRESHOLD_HIGH = 9_999.99
FEATURE_SET_VERSION = "fs-1.3"

SIM_START = datetime(2026, 1, 5, tzinfo=UTC)
SIM_DAYS = 182
