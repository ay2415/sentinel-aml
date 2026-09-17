"""Batch ingestion: read -> schema/row validation -> clean -> dedupe -> quality gates -> idempotent load.

Local: files are pulled from data/raw. Production: the same `ingest_transactions_frame` runs in a
worker consuming Azure Event Hubs (streaming) or triggered by Blob Storage events (batch drops).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.data.constants import VALID_CURRENCIES
from app.data.validation import (
    ACCOUNT_REQUIRED,
    QualityGates,
    SchemaError,
    parse_timestamps,
    require_columns,
    summarise_amounts,
    validate_customers,
    validate_transactions,
)
from app.database.models import Account, Customer, IngestionBatch, RejectedRecord, Tenant, utcnow
from app.security import prompt_guard
from app.security.prompt_guard import normalise

log = logging.getLogger(__name__)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _finish(db: Session, batch: IngestionBatch, status: str, report: dict, loaded=0, rejected=0, dupes=0) -> IngestionBatch:
    batch.status, batch.quality_report = status, report
    batch.rows_loaded, batch.rows_rejected, batch.rows_duplicate = loaded, rejected, dupes
    batch.finished_at = utcnow()
    db.flush()
    log_event(log, "ingestion_batch_finished", batch_id=batch.id, entity=batch.entity, status=status,
              loaded=loaded, rejected=rejected, duplicates=dupes)
    return batch


def ensure_tenants(db: Session) -> None:
    from app.data.constants import TENANTS

    for tid, name in TENANTS.items():
        if db.get(Tenant, tid) is None:
            db.add(Tenant(id=tid, name=name))
    db.flush()


def ingest_customers_and_accounts(db: Session, customers_csv: bytes, accounts_csv: bytes, source: str, actor: str = "system") -> dict:
    tenants = set(db.execute(select(Tenant.id)).scalars())
    cdf = pd.read_csv(io.BytesIO(customers_csv), dtype=str, keep_default_na=False)
    batch = IngestionBatch(entity="customers", source_name=source, file_sha256=sha256_bytes(customers_csv),
                           status="RUNNING", rows_received=len(cdf), triggered_by=actor)
    db.add(batch)
    db.flush()
    res = validate_customers(cdf, tenants)
    existing = set(db.execute(select(Customer.id)).scalars())
    new = res.valid[~res.valid["customer_id"].isin(existing)]
    db.bulk_insert_mappings(Customer, [{
        "id": r.customer_id, "tenant_id": r.tenant_id, "full_name": r.full_name, "segment": r.segment, "country": r.country,
        "kyc_risk_rating": r.kyc_risk_rating, "pep_flag": str(r.pep_flag).lower() == "true", "occupation": r.occupation,
        "expected_monthly_turnover_eur": float(r.expected_monthly_turnover_eur), "onboarded_at": r.onboarded_parsed.to_pydatetime(),
    } for r in new.itertuples()])
    _store_rejects(db, batch, res.rejected)
    _finish(db, batch, "SUCCEEDED", res.checks, loaded=len(new), rejected=len(res.rejected), dupes=len(res.valid) - len(new))

    adf = pd.read_csv(io.BytesIO(accounts_csv), dtype=str, keep_default_na=False)
    require_columns(adf, ACCOUNT_REQUIRED)
    abatch = IngestionBatch(entity="accounts", source_name=source, file_sha256=sha256_bytes(accounts_csv), status="RUNNING",
                            rows_received=len(adf), triggered_by=actor)
    db.add(abatch)
    db.flush()
    known_customers = dict(db.execute(select(Customer.id, Customer.tenant_id)).all())
    adf["opened_parsed"] = parse_timestamps(adf["opened_at"])
    ok = adf["customer_id"].map(known_customers).eq(adf["tenant_id"]) & adf["opened_parsed"].notna()
    existing_acc = set(db.execute(select(Account.id)).scalars())
    new_acc = adf[ok & ~adf["account_id"].isin(existing_acc)]
    db.bulk_insert_mappings(Account, [{
        "id": r.account_id, "tenant_id": r.tenant_id, "customer_id": r.customer_id, "iban": r.iban,
        "account_type": r.account_type, "currency": r.currency, "opened_at": r.opened_parsed.to_pydatetime(),
    } for r in new_acc.itertuples()])
    _store_rejects(db, abatch, adf.loc[~ok, ACCOUNT_REQUIRED].assign(reject_reason="invalid_customer_or_date"))
    _finish(db, abatch, "SUCCEEDED", {"rows_in": len(adf)}, loaded=len(new_acc), rejected=int((~ok).sum()))
    return {"customers_loaded": len(new), "accounts_loaded": len(new_acc)}


def _store_rejects(db: Session, batch: IngestionBatch, rejected: pd.DataFrame, cap: int = 5000) -> None:
    rows = rejected.head(cap).astype(object).where(rejected.head(cap).notna(), "").astype(str).to_dict(orient="records")
    db.bulk_insert_mappings(RejectedRecord, [{"batch_id": batch.id, "reason": r.pop("reject_reason"), "raw": r} for r in rows])


def clean_transactions(valid: pd.DataFrame, known_accounts: dict[str, tuple[str, str]]) -> tuple[pd.DataFrame, dict]:
    df = valid.copy()
    fx = df["currency"].map(VALID_CURRENCIES)
    df["amount_eur"] = (df["amount_num"] * fx).round(2)
    df["original_amount"] = df["amount_num"].round(2)
    df["customer_id"] = df["account_id"].map(lambda a: known_accounts[a][1])
    missing_country = df["counterparty_country"].isna() | (df["counterparty_country"].astype(str).str.strip() == "")
    df["counterparty_country"] = df["counterparty_country"].where(~missing_country, "ZZ").astype(str).str.upper().str[:2]
    df["reference_text"] = df["reference"].fillna("").astype(str).map(normalise).str.slice(0, 140)  # SEPA remittance limit
    # scan every customer-controlled reference ONCE at ingestion, so downstream safety never depends on which rows an agent reads
    df["reference_flags"] = df["reference_text"].map(lambda t: [f.rule for f in prompt_guard.scan(t)] if t else [])
    before = len(df)
    df = df.drop_duplicates(subset=["transaction_id"], keep="first")
    exact_dupes = before - len(df)
    fp_cols = ["account_id", "amount_eur", "direction", "counterparty_id"]
    df["_ts_sec"] = df["ts_parsed"].dt.floor("s")
    near = df.duplicated(subset=fp_cols + ["_ts_sec"], keep="first")
    df = df[~near]
    return df, {"in_file_duplicate_ids": int(exact_dupes), "near_duplicates_new_id": int(near.sum()),
                "missing_country_imputed_ZZ": int(missing_country.sum())}


def ingest_transactions_frame(db: Session, raw: pd.DataFrame, source: str, file_sha: str, actor: str = "system",
                              gates: QualityGates | None = None, now: pd.Timestamp | None = None) -> IngestionBatch:
    gates = gates or QualityGates()
    now = now or pd.Timestamp.now(tz="UTC")
    batch = IngestionBatch(entity="transactions", source_name=source, file_sha256=file_sha, status="RUNNING",
                           rows_received=len(raw), triggered_by=actor)
    db.add(batch)
    db.flush()
    try:
        known = {a: (t, c) for a, t, c in db.execute(select(Account.id, Account.tenant_id, Account.customer_id)).all()}
        res = validate_transactions(raw, known, now)
    except SchemaError as exc:
        return _finish(db, batch, "FAILED_SCHEMA", {"error": str(exc)})

    clean, dedupe_stats = clean_transactions(res.valid, known)
    report = {**res.checks, **dedupe_stats, **summarise_amounts(clean)}
    n = max(1, len(raw))
    report["reject_rate"] = round(len(res.rejected) / n, 5)
    report["missing_country_rate"] = round(dedupe_stats["missing_country_imputed_ZZ"] / n, 5)
    failures = []
    if report["reject_rate"] > gates.max_reject_rate:
        failures.append("reject_rate")
    if report["missing_country_rate"] > gates.max_missing_country_rate:
        failures.append("missing_country_rate")
    if (dedupe_stats["in_file_duplicate_ids"] + dedupe_stats["near_duplicates_new_id"]) / n > gates.max_duplicate_rate:
        failures.append("duplicate_rate")
    report["gate_failures"] = failures
    report["references_with_injection_flags"] = int((clean["reference_flags"].map(len) > 0).sum())
    _store_rejects(db, batch, res.rejected)
    if failures:  # quarantine: nothing from a bad batch reaches analytical tables
        return _finish(db, batch, "FAILED_QUALITY_GATE", report, rejected=len(res.rejected))

    records = pd.DataFrame({
        "id": clean["transaction_id"].astype(str), "tenant_id": clean["tenant_id"], "account_id": clean["account_id"],
        "customer_id": clean["customer_id"], "ts": clean["ts_parsed"], "amount_eur": clean["amount_eur"],
        "original_amount": clean["original_amount"], "original_currency": clean["currency"], "direction": clean["direction"],
        "channel": clean["channel"], "counterparty_id": clean["counterparty_id"].astype(str).str.slice(0, 64),
        "counterparty_country": clean["counterparty_country"], "reference_text": clean["reference_text"], "reference_flags": clean["reference_flags"], "batch_id": batch.id,
    })
    inserted = _idempotent_load(db, records)
    already_present = len(records) - inserted
    report["already_loaded_ids_skipped"] = already_present
    dupes = dedupe_stats["in_file_duplicate_ids"] + dedupe_stats["near_duplicates_new_id"] + already_present
    return _finish(db, batch, "SUCCEEDED", report, loaded=inserted, rejected=len(res.rejected), dupes=dupes)


def _idempotent_load(db: Session, records: pd.DataFrame) -> int:
    """INSERT ... ON CONFLICT DO NOTHING so replays of the same batch/event never double count."""
    if records.empty:
        return 0
    conn = db.connection()
    cols = list(records.columns)
    if conn.dialect.name == "postgresql":
        conn.execute(text("CREATE TEMP TABLE IF NOT EXISTS stg_transactions (LIKE transactions INCLUDING DEFAULTS) ON COMMIT DROP"))
        conn.execute(text("TRUNCATE stg_transactions"))
        buf = io.StringIO()
        out = records.copy()
        out["ts"] = out["ts"].map(lambda t: t.isoformat())
        out["reference_flags"] = out["reference_flags"].map(json.dumps)
        out.to_csv(buf, index=False, header=False)
        buf.seek(0)
        raw = conn.connection.dbapi_connection
        with raw.cursor() as cur:
            cur.copy_expert(f"COPY stg_transactions ({','.join(cols)}) FROM STDIN WITH (FORMAT csv, FORCE_NOT_NULL (reference_text))", buf)
        # column list comes from the code-defined DataFrame schema, never from user input; values are loaded via COPY
        col_sql = ",".join(cols)
        upsert = f"INSERT INTO transactions ({col_sql}) SELECT {col_sql} FROM stg_transactions ON CONFLICT (id) DO NOTHING"  # noqa: S608  # nosec B608
        res = conn.execute(text(upsert))
        return int(res.rowcount)
    from app.database.models import Transaction

    total = 0
    recs = records.assign(ts=records["ts"].map(lambda t: t.to_pydatetime())).to_dict(orient="records")
    for i in range(0, len(recs), 5000):
        res = conn.execute(Transaction.__table__.insert().prefix_with("OR IGNORE"), recs[i:i + 5000])
        total += res.rowcount
    return total


def ingest_directory(db: Session, raw_dir: Path, actor: str = "system") -> dict:
    ensure_tenants(db)
    out = ingest_customers_and_accounts(db, (raw_dir / "customers.csv").read_bytes(), (raw_dir / "accounts.csv").read_bytes(),
                                        source=str(raw_dir.name), actor=actor)
    tb = (raw_dir / "transactions.csv").read_bytes()
    raw = pd.read_csv(io.BytesIO(tb), dtype=str, keep_default_na=False, na_values=[""])
    batch = ingest_transactions_frame(db, raw, source="transactions.csv", file_sha=sha256_bytes(tb), actor=actor)
    out["transactions_batch"] = {"id": batch.id, "status": batch.status, "loaded": batch.rows_loaded,
                                 "rejected": batch.rows_rejected, "duplicates": batch.rows_duplicate, "report": batch.quality_report}
    return out
