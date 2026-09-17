"""Declarative schema + row-level validation producing a machine-readable data quality report."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.data.constants import CHANNELS, VALID_CURRENCIES

TXN_REQUIRED = ["transaction_id", "tenant_id", "account_id", "timestamp", "amount", "currency", "direction",
                "channel", "counterparty_id", "counterparty_country", "reference"]
CUSTOMER_REQUIRED = ["customer_id", "tenant_id", "full_name", "segment", "country", "kyc_risk_rating", "pep_flag",
                     "occupation", "expected_monthly_turnover_eur", "onboarded_at"]
ACCOUNT_REQUIRED = ["account_id", "tenant_id", "customer_id", "iban", "account_type", "currency", "opened_at"]


@dataclass
class QualityGates:
    max_reject_rate: float = 0.02
    max_missing_country_rate: float = 0.05
    max_duplicate_rate: float = 0.05


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    rejected: pd.DataFrame  # original columns + reject_reason
    checks: dict = field(default_factory=dict)


class SchemaError(ValueError):
    pass


def require_columns(df: pd.DataFrame, required: list[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SchemaError(f"missing required columns: {missing}")


def parse_timestamps(s: pd.Series) -> pd.Series:
    """ISO-8601 first, then the legacy day-first export format; anything else -> NaT."""
    iso = pd.to_datetime(s, errors="coerce", utc=True, format="ISO8601")
    legacy = pd.to_datetime(s.where(iso.isna()), errors="coerce", utc=True, format="%d/%m/%Y %H:%M:%S")
    return iso.fillna(legacy)


def validate_transactions(df: pd.DataFrame, known_accounts: dict[str, tuple[str, str]], now: pd.Timestamp) -> ValidationResult:
    """known_accounts: account_id -> (tenant_id, customer_id)."""
    require_columns(df, TXN_REQUIRED)
    df = df.copy()
    reasons = pd.Series("", index=df.index, dtype=object)

    def reject(mask: pd.Series, reason: str) -> None:
        reasons.loc[mask & (reasons == "")] = reason

    df["ts_parsed"] = parse_timestamps(df["timestamp"])
    df["amount_num"] = pd.to_numeric(df["amount"], errors="coerce")
    reject(df["transaction_id"].isna() | (df["transaction_id"].astype(str).str.len() > 64), "invalid_transaction_id")
    reject(df["ts_parsed"].isna(), "unparseable_timestamp")
    reject(df["ts_parsed"] > now, "future_timestamp")
    reject(df["amount_num"].isna(), "non_numeric_amount")
    reject(df["amount_num"] <= 0, "non_positive_amount")
    reject(~df["currency"].isin(list(VALID_CURRENCIES)), "unsupported_currency")
    reject(~df["direction"].isin(["in", "out"]), "invalid_direction")
    reject(~df["channel"].isin(CHANNELS), "invalid_channel")
    acct_tenant = df["account_id"].map(lambda a: known_accounts.get(a, (None, None))[0])
    reject(acct_tenant.isna(), "unknown_account")
    reject(acct_tenant.notna() & (acct_tenant != df["tenant_id"]), "tenant_account_mismatch")  # cross-tenant write attempt
    reject(df["amount_num"] > 5_000_000, "amount_exceeds_sanity_limit")

    ok = reasons == ""
    rejected = df.loc[~ok, TXN_REQUIRED].assign(reject_reason=reasons[~ok])
    valid = df.loc[ok].copy()
    checks = {
        "rows_in": int(len(df)),
        "rejected_by_reason": reasons[~ok].value_counts().to_dict(),
        "missing_counterparty_country": int(valid["counterparty_country"].isna().sum()),
        "missing_reference": int(valid["reference"].isna().sum()),
        "amount_eur_p50": None, "amount_eur_p99": None,
    }
    return ValidationResult(valid=valid, rejected=rejected, checks=checks)


def validate_customers(df: pd.DataFrame, known_tenants: set[str]) -> ValidationResult:
    require_columns(df, CUSTOMER_REQUIRED)
    df = df.copy()
    reasons = pd.Series("", index=df.index, dtype=object)
    df["onboarded_parsed"] = parse_timestamps(df["onboarded_at"])
    for mask, reason in [
        (~df["tenant_id"].isin(known_tenants), "unknown_tenant"),
        (~df["kyc_risk_rating"].isin(["low", "medium", "high"]), "invalid_kyc_rating"),
        (df["onboarded_parsed"].isna(), "invalid_onboarded_at"),
        (pd.to_numeric(df["expected_monthly_turnover_eur"], errors="coerce").fillna(-1) < 0, "invalid_turnover"),
        (df["customer_id"].duplicated(keep="first"), "duplicate_customer_id"),
    ]:
        reasons.loc[mask & (reasons == "")] = reason
    ok = reasons == ""
    return ValidationResult(valid=df.loc[ok], rejected=df.loc[~ok, CUSTOMER_REQUIRED].assign(reject_reason=reasons[~ok]),
                            checks={"rows_in": len(df), "rejected_by_reason": reasons[~ok].value_counts().to_dict()})


def summarise_amounts(valid: pd.DataFrame) -> dict:
    a = valid["amount_eur"].to_numpy()
    if len(a) == 0:
        return {}
    return {"amount_eur_p50": float(np.percentile(a, 50)), "amount_eur_p99": float(np.percentile(a, 99)),
            "amount_eur_max": float(a.max())}
