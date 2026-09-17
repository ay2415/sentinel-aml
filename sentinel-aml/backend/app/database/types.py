"""Portable column types: pgvector/JSONB on PostgreSQL, JSON on SQLite (unit tests)."""
from __future__ import annotations

import numpy as np
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

JsonType = JSON().with_variant(JSONB(), "postgresql")


class EmbeddingVector(TypeDecorator):
    """pgvector `vector(dim)` on PostgreSQL; JSON float list elsewhere."""

    impl = JSON
    cache_ok = True

    class comparator_factory(TypeDecorator.Comparator):
        def cosine_distance(self, other):
            from sqlalchemy import Float

            return self.op("<=>", return_type=Float)(other)

    def __init__(self, dim: int):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        arr = [float(x) for x in value]
        if len(arr) != self.dim:
            raise ValueError(f"embedding has dim {len(arr)}, expected {self.dim}")
        return np.asarray(arr, dtype=np.float32) if dialect.name == "postgresql" else arr

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return np.asarray(value, dtype=np.float32)
