"""Training pipeline: point-in-time alert features + historical dispositions -> calibrated risk model.

Models trained and compared:
* Logistic Regression   - transparent linear baseline every model must beat.
* XGBoost               - production candidate for non-linear tabular interactions.
* Isotonic calibration  - scores must mean probabilities because thresholds/policies use them.
* Isolation Forest      - unsupervised novelty signal for patterns absent from historical labels.

Validation: temporal split (train on the past, test on the future) plus a customer-disjoint
GroupKFold robustness check, because the same customer can raise alerts in train and test.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text
from sqlalchemy.orm import Session
from xgboost import XGBClassifier

from app.core.logging import log_event
from app.data.features import FEATURE_NAMES
from app.data.rules import RULES
from app.ml import registry
from app.ml.calibration import PlattCalibrator
from app.ml.drift import reference_profile
from app.ml.metrics import classification_report, threshold_for_recall

log = logging.getLogger(__name__)
MODEL_NAME = "alert_risk"
RULE_CODES = [r.code for r in RULES]
MODEL_FEATURES = FEATURE_NAMES + [f"rule_{c}" for c in RULE_CODES]
TARGET_RECALL = 0.95
SEED = 7
XGB_PARAMS = dict(n_estimators=350, max_depth=4, learning_rate=0.05, subsample=0.85, colsample_bytree=0.8,
                  min_child_weight=3, reg_lambda=2.0, eval_metric="aucpr", random_state=SEED, n_jobs=1)


def build_matrix(df: pd.DataFrame) -> np.ndarray:
    feats = pd.DataFrame(list(df["features"]))
    for c in FEATURE_NAMES:
        if c not in feats:
            feats[c] = 0.0
    X = feats[FEATURE_NAMES].astype(float)
    for c in RULE_CODES:
        X[f"rule_{c}"] = (df["rule_code"].to_numpy() == c).astype(float)
    return X[MODEL_FEATURES].to_numpy()


def load_training_frame(db: Session, labels_csv: Path) -> pd.DataFrame:
    alerts = pd.read_sql(text("SELECT id, tenant_id, customer_id, rule_code, window_end, features FROM alerts"), db.connection())
    labels = pd.read_csv(labels_csv)
    df = alerts.merge(labels[["customer_id", "is_suspicious", "typology", "onset_date", "persona"]], on="customer_id", how="left")
    df["window_end"] = pd.to_datetime(df["window_end"], utc=True)
    onset = pd.to_datetime(df["onset_date"], utc=True)
    # label = the historical investigation outcome: suspicious activity had started by the alert date
    df["y"] = ((df["is_suspicious"] == 1) & onset.notna() & (onset <= df["window_end"])).astype(int)
    return df.sort_values("window_end").reset_index(drop=True)


def temporal_split(df: pd.DataFrame, train_q=0.6, calib_q=0.75):
    t1, t2 = df["window_end"].quantile(train_q), df["window_end"].quantile(calib_q)
    return df[df["window_end"] <= t1], df[(df["window_end"] > t1) & (df["window_end"] <= t2)], df[df["window_end"] > t2]


def _fit_xgb(X, y):
    spw = float((y == 0).sum() / max(1, (y == 1).sum()))
    return XGBClassifier(**XGB_PARAMS, scale_pos_weight=spw).fit(X, y)


def group_cv(df: pd.DataFrame, folds: int = 5) -> dict:
    X, y, groups = build_matrix(df), df["y"].to_numpy(), df["customer_id"].to_numpy()
    from sklearn.metrics import average_precision_score, roc_auc_score
    aucs, aps = [], []
    for tr, te in GroupKFold(n_splits=folds).split(X, y, groups):
        if y[te].sum() == 0:
            continue
        p = _fit_xgb(X[tr], y[tr]).predict_proba(X[te])[:, 1]
        aucs.append(roc_auc_score(y[te], p))
        aps.append(average_precision_score(y[te], p))
    return {"folds": len(aucs), "roc_auc_mean": float(np.mean(aucs)), "roc_auc_std": float(np.std(aucs)),
            "pr_auc_mean": float(np.mean(aps)), "pr_auc_std": float(np.std(aps))}


def train(db: Session, labels_csv: Path, promote_if_better: bool = True) -> dict:
    df = load_training_frame(db, labels_csv)
    if df["y"].sum() < 20:
        raise ValueError("not enough positive labels to train")
    train_df, calib_df, test_df = temporal_split(df)
    Xtr = build_matrix(train_df)  # training-period matrix: Isolation Forest + drift reference profile
    Xte, yte = build_matrix(test_df), test_df["y"].to_numpy()
    data_hash = hashlib.sha256(pd.util.hash_pandas_object(df[["id", "y"]], index=False).values.tobytes()).hexdigest()

    # ---- candidate: out-of-fold (customer-grouped) predictions over the whole training period give ~5x more
    # positives for calibration + threshold selection than a single hold-out slice -> a stable operating point.
    dev_df = pd.concat([train_df, calib_df])
    Xdev, ydev, gdev = build_matrix(dev_df), dev_df["y"].to_numpy(), dev_df["customer_id"].to_numpy()
    oof = np.zeros(len(ydev))
    for tr, va in GroupKFold(n_splits=5).split(Xdev, ydev, gdev):
        oof[va] = _fit_xgb(Xdev[tr], ydev[tr]).predict_proba(Xdev[va])[:, 1]
    # ---- baseline: identical protocol (same dev data, same OOF threshold selection) for a fair comparison
    def _lr():
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced", C=0.5))
    lr_oof = np.zeros(len(ydev))
    for tr, va in GroupKFold(n_splits=5).split(Xdev, ydev, gdev):
        lr_oof[va] = _lr().fit(Xdev[tr], ydev[tr]).predict_proba(Xdev[va])[:, 1]
    lr = _lr().fit(Xdev, ydev)
    lr_report = classification_report(yte, lr.predict_proba(Xte)[:, 1], threshold_for_recall(ydev, lr_oof, TARGET_RECALL))
    xgb = _fit_xgb(Xdev, ydev)
    raw_te = xgb.predict_proba(Xte)[:, 1]
    uncal_report = classification_report(yte, raw_te, threshold_for_recall(ydev, oof, TARGET_RECALL))
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof, ydev)  # comparison only
    iso_report = classification_report(yte, iso.predict(raw_te), threshold_for_recall(ydev, iso.predict(oof), TARGET_RECALL))
    platt = PlattCalibrator().fit(oof, ydev)
    thr = threshold_for_recall(ydev, platt.predict(oof), TARGET_RECALL)
    cal_te = platt.predict(raw_te)
    xgb_report = classification_report(yte, cal_te, thr)
    # transparency: the recall/workload trade-off on the untouched test period (reported, never used for selection)
    operating_curve = []
    for target in (0.90, 0.95, 0.98, 0.99):
        t_dev = threshold_for_recall(ydev, platt.predict(oof), target)
        operating_curve.append({"target_recall_on_dev": target, "threshold": round(t_dev, 4),
                                "test_recall": round(float(((cal_te >= t_dev) & (yte == 1)).sum() / max(1, yte.sum())), 4),
                                "test_deprioritised_share": round(float((cal_te < t_dev).mean()), 4),
                                "test_precision": round(float(yte[cal_te >= t_dev].mean()) if (cal_te >= t_dev).any() else 0.0, 4)})

    # ---- per-rule and per-tenant breakdown on the test period
    breakdown = {}
    for key in ("rule_code", "tenant_id"):
        breakdown[key] = {}
        for val, g in test_df.assign(p=cal_te).groupby(key):
            yy, pp = g["y"].to_numpy(), g["p"].to_numpy()
            breakdown[key][val] = {"n": int(len(g)), "positives": int(yy.sum()),
                                   "recall_at_threshold": float(((pp >= thr) & (yy == 1)).sum() / yy.sum()) if yy.sum() else None,
                                   "deprioritised_share": float((pp < thr).mean())}

    # ---- unsupervised novelty model (fit on training period only, no labels)
    iforest = IsolationForest(n_estimators=300, contamination="auto", random_state=SEED).fit(Xtr[:, : len(FEATURE_NAMES)])
    train_scores = np.sort(-iforest.score_samples(Xtr[:, : len(FEATURE_NAMES)]))
    te_anom = -iforest.score_samples(Xte[:, : len(FEATURE_NAMES)])
    from sklearn.metrics import roc_auc_score
    iforest_auc = float(roc_auc_score(yte, te_anom)) if 0 < yte.sum() < len(yte) else None

    gcv = group_cv(df)
    importance = sorted(zip(MODEL_FEATURES, xgb.feature_importances_.tolist(), strict=True), key=lambda t: -t[1])[:15]
    version = datetime.now(UTC).strftime("v%Y%m%d%H%M%S")
    metrics = {
        "split": {"train": len(train_df), "calibration": len(calib_df), "test": len(test_df),
                  "train_end": str(train_df["window_end"].max()), "test_start": str(test_df["window_end"].min())},
        "target_recall": TARGET_RECALL, "logistic_regression_test": lr_report, "xgboost_uncalibrated_test": uncal_report,
        "xgboost_calibrated_test": xgb_report, "xgboost_isotonic_test_comparison": iso_report, "operating_curve_test": operating_curve,
        "threshold_selection": "5-fold customer-grouped out-of-fold predictions over train+calibration period", "breakdown_test": breakdown, "isolation_forest_test_roc_auc": iforest_auc,
        "customer_group_cv_xgboost": gcv, "top_feature_importance": importance,
    }
    gates = {
        "beats_baseline_pr_auc": xgb_report["pr_auc"] >= lr_report["pr_auc"],
        "test_recall_at_threshold_ge_0.90": xgb_report["recall"] >= 0.90,
        "group_cv_roc_auc_ge_0.80": gcv["roc_auc_mean"] >= 0.80,
    }
    metrics["promotion_gates"] = gates
    artifact = {"xgb": xgb, "calibrator": platt, "iforest": iforest, "iforest_train_scores": train_scores,
                "features": MODEL_FEATURES, "base_features": FEATURE_NAMES, "rule_codes": RULE_CODES, "threshold": thr}
    params = {"xgboost": XGB_PARAMS, "calibration": "platt_sigmoid", "iforest": {"n_estimators": 300}, "seed": SEED}
    lineage = {"name": MODEL_NAME, "version": version, "training_data_hash": data_hash, "labels_file": labels_csv.name,
               "feature_set": FEATURE_NAMES, "params": params, "metrics": metrics,
               "reference_profile": reference_profile(pd.DataFrame(Xtr[:, : len(FEATURE_NAMES)], columns=FEATURE_NAMES),
                                                      xgb.predict_proba(Xtr)[:, 1])}
    path, sha = registry.save_artifact(MODEL_NAME, version, artifact, lineage)
    mv = registry.register(db, name=MODEL_NAME, version=version, algorithm="xgboost+platt+isolation_forest", path=path, sha=sha,
                           training_data_hash=data_hash, feature_names=MODEL_FEATURES, params=params,
                           metrics={**metrics, "reference_profile": lineage["reference_profile"]})
    promoted = False
    if promote_if_better and all(gates.values()):
        registry.promote(db, mv.id)
        promoted = True
    log_event(log, "model_trained", model=MODEL_NAME, version=version, promoted=promoted, pr_auc=xgb_report["pr_auc"])
    return {"model_version_id": mv.id, "version": version, "promoted": promoted, "metrics": metrics}
