"""Embedding providers behind one interface.

* GloveSIFEmbedder (local default): pretrained GloVe 300d word vectors with SIF frequency
  weighting (Arora et al., 2017). Runs offline on CPU, no GPU or external API needed.
* BGEEmbedder (production): BAAI/bge-base-en-v1.5 via sentence-transformers (768d), a strong
  open retrieval model that can be self-hosted, which keeps regulated data inside the tenant
  boundary. Switching providers requires re-indexing and a matching EMBEDDING_DIM.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

import numpy as np

from app.core.config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


class Embedder:
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class GloveSIFEmbedder(Embedder):
    def __init__(self, a: float = 1e-3):
        d = get_settings().embedding_cache_dir
        vec_path, vocab_path = d / "glove300_100k.npy", d / "glove300_100k_vocab.json"
        if not vec_path.exists():
            raise FileNotFoundError(f"{vec_path} missing: run `python scripts/download_embeddings.py`")
        self.vectors = np.load(vec_path, mmap_mode="r")
        vocab = json.loads(vocab_path.read_text())
        self.index = {w: i for i, w in enumerate(vocab)}
        # GloVe vocab is frequency ordered -> Zipf estimate of p(w) for SIF weights a / (a + p(w))
        ranks = np.arange(1, len(vocab) + 1)
        p = (1.0 / ranks) / np.sum(1.0 / ranks)
        self.weights = a / (a + p)
        self.name, self.dim = "glove-wiki-gigaword-300-sif", self.vectors.shape[1]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            idx = [self.index[w] for w in _TOKEN.findall(t.lower()) if w in self.index]
            if idx:
                v = (self.vectors[idx] * self.weights[idx][:, None]).sum(axis=0)
                n = np.linalg.norm(v)
                out[i] = v / n if n > 0 else v
        return out


class HashingEmbedder(Embedder):
    """Download-free fallback for unit tests/CI only (lexical signal, no semantics). Never used for evaluation."""

    def __init__(self, dim: int):
        from sklearn.feature_extraction.text import HashingVectorizer

        self.vec = HashingVectorizer(n_features=dim, alternate_sign=False, norm="l2", ngram_range=(1, 2))
        self.name, self.dim = f"hashing-{dim}", dim

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.vec.transform(texts).toarray().astype(np.float32)


class BGEEmbedder(Embedder):  # pragma: no cover - requires model download (blocked in the build sandbox)
    def __init__(self):
        from sentence_transformers import SentenceTransformer

        s = get_settings()
        self.model = SentenceTransformer(s.embedding_model)
        self.name, self.dim = s.embedding_model, self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)


@lru_cache
def get_embedder() -> Embedder:
    provider = get_settings().embedding_provider
    if provider == "bge":
        emb = BGEEmbedder()
    elif provider == "hashing":
        emb = HashingEmbedder(get_settings().embedding_dim)
    else:
        emb = GloveSIFEmbedder()
    if emb.dim != get_settings().embedding_dim:
        raise ValueError(f"EMBEDDING_DIM={get_settings().embedding_dim} but {emb.name} produces {emb.dim}")
    return emb
