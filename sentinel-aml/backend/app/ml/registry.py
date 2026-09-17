"""File-based model registry + DB metadata.

Local: artifacts under models/registry/<name>/<version>/ with metadata.json.
Production: MLflow Model Registry or Azure ML registry, artifacts in Blob Storage.
The contract that matters is identical: immutable versioned artifacts, content hash verified
before loading (pickles can execute code), stage transitions, and full training lineage.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.models import ModelVersion


class ArtifactIntegrityError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_artifact(name: str, version: str, obj, metadata: dict) -> tuple[Path, str]:
    d = get_settings().models_dir / "registry" / name / version
    d.mkdir(parents=True, exist_ok=True)
    path = d / "model.joblib"
    joblib.dump(obj, path)
    sha = file_sha256(path)
    (d / "metadata.json").write_text(json.dumps({**metadata, "artifact_sha256": sha}, indent=2, default=str))
    return path, sha


def register(db: Session, *, name: str, version: str, algorithm: str, path: Path, sha: str, training_data_hash: str,
             feature_names: list[str], params: dict, metrics: dict, stage: str = "staging") -> ModelVersion:
    mv = ModelVersion(name=name, version=version, algorithm=algorithm, artifact_uri=str(path.relative_to(get_settings().models_dir)),
                      artifact_sha256=sha, training_data_hash=training_data_hash, feature_names=feature_names,
                      params=params, metrics=metrics, stage=stage)
    db.add(mv)
    db.flush()
    return mv


def promote(db: Session, model_version_id: str) -> None:
    mv = db.get(ModelVersion, model_version_id)
    db.execute(update(ModelVersion).where(ModelVersion.name == mv.name, ModelVersion.stage == "production").values(stage="archived"))
    mv.stage = "production"
    db.flush()


def get_production(db: Session, name: str) -> ModelVersion | None:
    return db.execute(select(ModelVersion).where(ModelVersion.name == name, ModelVersion.stage == "production")
                      .order_by(ModelVersion.created_at.desc())).scalars().first()


def load_verified(mv: ModelVersion):
    path = get_settings().models_dir / mv.artifact_uri
    if not path.exists():
        raise ArtifactIntegrityError(f"artifact missing: {path}")
    actual = file_sha256(path)
    if actual != mv.artifact_sha256:
        raise ArtifactIntegrityError(f"artifact hash mismatch for {mv.name}:{mv.version}")
    return joblib.load(path)
