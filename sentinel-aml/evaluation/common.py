"""Shared evaluation helpers. Run scripts from the repository root: python evaluation/<script>.py"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
REPORTS = ROOT / "evaluation" / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)


def pct(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q)) if len(values) else None


def write_report(name: str, payload: dict) -> Path:
    payload = {"generated_at": datetime.now(UTC).isoformat(), **payload}
    path = REPORTS / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
