"""Point-in-time behavioural features.

The SAME function computes features for (a) historical training alerts and (b) live alerts,
which prevents training/serving skew. Features only use transactions with ts <= as_of,
which prevents label leakage from the future.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.data.constants import FEATURE_SET_VERSION, HIGH_RISK_COUNTRIES, NEAR_THRESHOLD_HIGH, NEAR_THRESHOLD_LOW, SIM_START

DAY = np.int64(86_400_000_000_000)  # ns

FEATURE_NAMES = [
    "txn_count_7d", "txn_count_30d", "total_in_7d", "total_out_7d", "total_in_30d", "total_out_30d",
    "out_in_ratio_30d", "pass_through_ratio_7d", "rapid_in_out_days_30d", "cash_in_amount_30d", "cash_deposit_count_30d",
    "near_threshold_cash_count_30d", "cash_in_share_30d", "unique_cash_branches_30d", "unique_counterparties_in_30d", "unique_counterparties_out_30d",
    "high_risk_country_amount_30d", "high_risk_country_txn_count_30d", "intl_wire_out_amount_30d", "crypto_out_amount_30d",
    "avg_txn_amount_30d", "max_txn_amount_30d", "amount_cv_30d", "velocity_ratio_30d", "night_txn_share_30d",
    "unknown_country_share_30d", "dormancy_gap_days", "account_age_days", "turnover_vs_expected_30d",
    "kyc_risk_high", "kyc_risk_medium", "pep_flag", "segment_sme",
]


@dataclass
class CustomerArrays:
    """Columnar, time-sorted view of one customer's transactions for fast window slicing."""
    ts: np.ndarray          # int64 ns
    amount: np.ndarray      # float64 EUR
    is_in: np.ndarray       # bool
    channel: np.ndarray     # object
    high_risk: np.ndarray   # bool
    unknown_country: np.ndarray
    counterparty: np.ndarray
    hour: np.ndarray

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> CustomerArrays:
        df = df.sort_values("ts")
        ts = pd.to_datetime(df["ts"], utc=True)
        return cls(
            ts=ts.dt.as_unit("ns").astype("int64").to_numpy(), amount=df["amount_eur"].astype(float).to_numpy(),
            is_in=(df["direction"] == "in").to_numpy(), channel=df["channel"].to_numpy(dtype=object),
            high_risk=df["counterparty_country"].isin(HIGH_RISK_COUNTRIES).to_numpy(),
            unknown_country=(df["counterparty_country"] == "ZZ").to_numpy(),
            counterparty=df["counterparty_id"].to_numpy(dtype=object), hour=ts.dt.hour.to_numpy(),
        )


def _sum(x: np.ndarray) -> float:
    return float(x.sum()) if x.size else 0.0


def compute_features(arr: CustomerArrays, customer: dict, as_of: pd.Timestamp,
                     observation_start: pd.Timestamp = pd.Timestamp(SIM_START)) -> dict:
    t_end = np.int64(pd.Timestamp(as_of).as_unit("ns").value)
    hi = int(np.searchsorted(arr.ts, t_end, side="right"))
    lo30 = int(np.searchsorted(arr.ts, t_end - 30 * DAY, side="left"))
    lo7 = int(np.searchsorted(arr.ts, t_end - 7 * DAY, side="left"))
    w = slice(lo30, hi)
    w7 = slice(lo7, hi)
    amt, inn, ch = arr.amount[w], arr.is_in[w], arr.channel[w]
    amt7, inn7 = arr.amount[w7], arr.is_in[w7]
    tin30, tout30 = _sum(amt[inn]), _sum(amt[~inn])
    tin7, tout7 = _sum(amt7[inn7]), _sum(amt7[~inn7])
    is_cash_in = (ch == "cash_deposit") & inn
    cash_amt = amt[is_cash_in]

    # rapid in/out: days where >= EUR1000 came in and >= 80% of it left within the same or next day
    days = (arr.ts[w] // DAY)
    rapid = 0
    if days.size:
        d_in = pd.Series(amt[inn]).groupby(days[inn]).sum()
        d_out = pd.Series(amt[~inn]).groupby(days[~inn]).sum()
        for d, v in d_in.items():
            if v >= 1000 and (d_out.get(d, 0.0) + d_out.get(d + 1, 0.0)) >= 0.8 * v:
                rapid += 1

    # velocity vs prior behaviour; only defined when >= 30 days of observed history exist
    obs_start = np.int64(pd.Timestamp(observation_start).as_unit("ns").value)
    hist_start = max(obs_start, np.int64(pd.Timestamp(customer["onboarded_at"]).as_unit("ns").value))
    prior_days = (t_end - 30 * DAY - hist_start) / DAY
    if prior_days >= 30:
        prior_count = lo30 - int(np.searchsorted(arr.ts, max(t_end - 120 * DAY, hist_start), side="left"))
        velocity = (hi - lo30) / (prior_count / (min(prior_days, 90.0) / 30.0) + 1.0)
    else:
        velocity = 1.0

    # dormancy: gap before the first transaction in the window (bounded by what we could observe)
    if hi > lo30:
        prev_ts = arr.ts[lo30 - 1] if lo30 > 0 else hist_start
        gap = float((arr.ts[lo30] - max(prev_ts, hist_start)) / DAY)
    else:
        gap = 30.0
    n = max(1, hi - lo30)
    out_mask = ~inn
    return {
        "txn_count_7d": float(hi - lo7), "txn_count_30d": float(hi - lo30),
        "total_in_7d": tin7, "total_out_7d": tout7, "total_in_30d": tin30, "total_out_30d": tout30,
        "out_in_ratio_30d": tout30 / tin30 if tin30 > 0 else 0.0,
        "pass_through_ratio_7d": min(tout7, tin7) / tin7 if tin7 > 0 else 0.0,
        "rapid_in_out_days_30d": float(rapid),
        "cash_in_amount_30d": _sum(cash_amt), "cash_deposit_count_30d": float(cash_amt.size),
        "near_threshold_cash_count_30d": float(((cash_amt >= NEAR_THRESHOLD_LOW) & (cash_amt <= NEAR_THRESHOLD_HIGH)).sum()),
        "cash_in_share_30d": _sum(cash_amt) / tin30 if tin30 > 0 else 0.0,
        # added in fs-1.3 after error analysis: funnel/structuring accounts spread cash across many branches
        "unique_cash_branches_30d": float(len(set(arr.counterparty[w][is_cash_in]))),
        "unique_counterparties_in_30d": float(len(set(arr.counterparty[w][inn]))),
        "unique_counterparties_out_30d": float(len(set(arr.counterparty[w][out_mask]))),
        "high_risk_country_amount_30d": _sum(amt[arr.high_risk[w]]),
        "high_risk_country_txn_count_30d": float(arr.high_risk[w].sum()),
        "intl_wire_out_amount_30d": _sum(amt[(ch == "intl_wire") & out_mask]),
        "crypto_out_amount_30d": _sum(amt[(ch == "crypto_exchange") & out_mask]),
        "avg_txn_amount_30d": float(amt.mean()) if amt.size else 0.0,
        "max_txn_amount_30d": float(amt.max()) if amt.size else 0.0,
        "amount_cv_30d": float(amt.std() / amt.mean()) if amt.size > 1 and amt.mean() > 0 else 0.0,
        "velocity_ratio_30d": float(velocity),
        "night_txn_share_30d": float(((arr.hour[w] <= 4)).sum() / n),
        "unknown_country_share_30d": float(arr.unknown_country[w].sum() / n),
        "dormancy_gap_days": gap,
        "account_age_days": float((t_end - pd.Timestamp(customer["onboarded_at"]).value) / DAY),
        "turnover_vs_expected_30d": tin30 / max(1.0, float(customer["expected_monthly_turnover_eur"])),
        "kyc_risk_high": float(customer["kyc_risk_rating"] == "high"),
        "kyc_risk_medium": float(customer["kyc_risk_rating"] == "medium"),
        "pep_flag": float(bool(customer["pep_flag"])),
        "segment_sme": float(customer["segment"] == "sme"),
    }


def feature_vector(features: dict) -> list[float]:
    return [float(features.get(k, 0.0)) for k in FEATURE_NAMES]


__all__ = ["FEATURE_NAMES", "FEATURE_SET_VERSION", "CustomerArrays", "compute_features", "feature_vector"]
