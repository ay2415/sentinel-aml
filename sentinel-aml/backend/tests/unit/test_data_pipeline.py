import pandas as pd
import pytest

from app.data.constants import SIM_START
from app.data.features import CustomerArrays, compute_features
from app.data.rules import RULES_BY_CODE, evaluate
from app.data.validation import QualityGates, SchemaError, parse_timestamps, validate_transactions

NOW = pd.Timestamp("2026-09-01", tz="UTC")
ACCTS = {"ACC-1": ("emerald", "CUS-1")}


def raw(**over):
    row = {"transaction_id": "TX-1", "tenant_id": "emerald", "account_id": "ACC-1", "timestamp": "2026-03-01T10:00:00+00:00",
           "amount": "100.00", "currency": "EUR", "direction": "in", "channel": "sepa", "counterparty_id": "CP-1",
           "counterparty_country": "IE", "reference": ""}
    row.update(over)
    return row


@pytest.mark.parametrize("override,reason", [
    ({"amount": "-5"}, "non_positive_amount"), ({"amount": "abc"}, "non_numeric_amount"),
    ({"currency": "XXX"}, "unsupported_currency"), ({"timestamp": "not-a-date"}, "unparseable_timestamp"),
    ({"timestamp": "2031-01-01T00:00:00+00:00"}, "future_timestamp"), ({"account_id": "ACC-404"}, "unknown_account"),
    ({"tenant_id": "liffey"}, "tenant_account_mismatch"), ({"direction": "sideways"}, "invalid_direction"),
    ({"channel": "carrier_pigeon"}, "invalid_channel"), ({"amount": "9000000"}, "amount_exceeds_sanity_limit"),
])
def test_row_validation_rejects_defects(override, reason):
    res = validate_transactions(pd.DataFrame([raw(**override)]), ACCTS, NOW)
    assert len(res.valid) == 0 and res.rejected.iloc[0]["reject_reason"] == reason


def test_schema_validation_missing_columns():
    with pytest.raises(SchemaError):
        validate_transactions(pd.DataFrame([{"transaction_id": "x"}]), ACCTS, NOW)


def test_legacy_day_first_timestamps_parse():
    s = parse_timestamps(pd.Series(["2026-03-01T10:00:00+00:00", "05/03/2026 13:22:00", "bad"]))
    assert s.iloc[1] == pd.Timestamp("2026-03-05 13:22:00", tz="UTC") and pd.isna(s.iloc[2])


def test_quality_gates_defaults_are_strict():
    assert QualityGates().max_reject_rate <= 0.02


def _arrays(rows):
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return CustomerArrays.from_frame(df)


CUST = {"onboarded_at": SIM_START - pd.Timedelta(days=400), "expected_monthly_turnover_eur": 3000, "kyc_risk_rating": "low",
        "pep_flag": False, "segment": "retail"}


def _tx(day, amount, direction="in", channel="cash_deposit", country="IE", cp="B1"):
    return {"ts": SIM_START + pd.Timedelta(days=day), "amount_eur": amount, "direction": direction, "channel": channel,
            "counterparty_id": cp, "counterparty_country": country}


def test_features_are_point_in_time():
    arr = _arrays([_tx(70, 9000), _tx(75, 9500), _tx(90, 9900)])  # the day-90 deposit is in the future at as_of=day 80
    f = compute_features(arr, CUST, pd.Timestamp(SIM_START + pd.Timedelta(days=80)))
    assert f["near_threshold_cash_count_30d"] == 2 and f["cash_in_amount_30d"] == 18500


def test_velocity_undefined_without_history_is_neutral():
    arr = _arrays([_tx(d, 50, "out", "card") for d in range(0, 20)])
    f = compute_features(arr, CUST, pd.Timestamp(SIM_START + pd.Timedelta(days=20)))
    assert f["velocity_ratio_30d"] == 1.0  # regression: previously spiked because missing history looked like zero activity


def test_high_risk_and_rules():
    arr = _arrays([_tx(70, 9000), _tx(72, 9500), _tx(74, 4000, "out", "intl_wire", "IR", "HRB")])
    f = compute_features(arr, CUST, pd.Timestamp(SIM_START + pd.Timedelta(days=80)))
    codes = {r.code for r in evaluate(f)}
    assert {"R01_STRUCTURING", "R03_HIGH_RISK_JURISDICTION", "R05_LARGE_CASH"} <= codes
    assert RULES_BY_CODE["R05_LARGE_CASH"].condition({**f, "segment_sme": 1.0}) is False  # SME threshold is higher
