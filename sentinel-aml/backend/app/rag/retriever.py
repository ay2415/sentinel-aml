"""Access-controlled hybrid retrieval: ACL pre-filter -> dense + BM25 -> RRF fusion -> rerank.

ACL filtering happens INSIDE the database query, before similarity ranking, so unauthorised
chunks are never candidates (post-filtering can leak through scores/counts and starve results).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.models import KBChunk, KBDocument
from app.observability import metrics as m
from app.rag.embeddings import get_embedder
from app.security import prompt_guard

_STOP = set("a an the of to and or in on for is are be by with as at from that this it its into not no any all per".split())
_TOK = re.compile(r"[a-z0-9]+")
MAX_QUERY_CHARS = 500
RRF_K = 60


@dataclass(frozen=True)
class AccessContext:
    tenant_id: str
    clearance: int


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    title: str
    section: str
    text: str
    doc_type: str
    classification_level: int
    tenant_id: str | None
    score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None


def tokens(text: str) -> list[str]:
    return [t for t in _TOK.findall(text.lower()) if t not in _STOP and len(t) > 1]


def acl_filter(ctx: AccessContext, doc_types: list[str] | None = None):
    conds = [KBChunk.is_active.is_(True), or_(KBChunk.tenant_id.is_(None), KBChunk.tenant_id == ctx.tenant_id),
             KBChunk.clearance_level <= ctx.clearance]
    if doc_types:
        conds.append(KBDocument.doc_type.in_(doc_types))
    return and_(*conds)


class Retriever:
    def __init__(self, candidate_k: int | None = None):
        self.candidate_k = candidate_k or get_settings().rag_candidate_k

    def search(self, db: Session, query: str, ctx: AccessContext, top_k: int = 6, mode: str = "hybrid_rerank",
               doc_types: list[str] | None = None) -> tuple[list[RetrievedChunk], dict]:
        t0 = time.perf_counter()
        query = prompt_guard.normalise(query)[:MAX_QUERY_CHARS]
        flags = [f.rule for f in prompt_guard.scan(query)]
        base = (select(KBChunk, KBDocument.title, KBDocument.doc_type).join(KBDocument, KBDocument.id == KBChunk.document_id)
                .where(acl_filter(ctx, doc_types)))
        rows = db.execute(base).all()  # permitted corpus (small KB; see docs/rag.md for scale-out)
        by_id = {r[0].id: r for r in rows}
        dense_ids: list[str] = []
        lexical_ids: list[str] = []
        if mode in ("dense", "hybrid", "hybrid_rerank") and rows:
            qv = get_embedder().embed([query])[0]
            if db.bind.dialect.name == "postgresql":
                q = base.with_only_columns(KBChunk.id).order_by(KBChunk.embedding.cosine_distance(qv)).limit(self.candidate_k)
                dense_ids = list(db.execute(q).scalars())
            else:
                mat = np.vstack([np.asarray(r[0].embedding, dtype=np.float32) for r in rows])
                sims = mat @ qv
                dense_ids = [rows[i][0].id for i in np.argsort(-sims)[: self.candidate_k]]
        if mode in ("lexical", "hybrid", "hybrid_rerank") and rows:
            corpus = [tokens(r[0].text) for r in rows]
            bm = BM25Okapi(corpus)
            scores = bm.get_scores(tokens(query))
            lexical_ids = [rows[i][0].id for i in np.argsort(-scores)[: self.candidate_k] if scores[i] > 0]

        fused: dict[str, float] = {}
        for ranked in (dense_ids, lexical_ids):
            for rank, cid in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        if mode == "hybrid_rerank":
            fused = self._rerank(query, fused, by_id)
        ordered = sorted(fused.items(), key=lambda t: -t[1])[:top_k]
        d_rank = {c: i + 1 for i, c in enumerate(dense_ids)}
        l_rank = {c: i + 1 for i, c in enumerate(lexical_ids)}
        results = []
        for cid, score in ordered:
            ch, title, doc_type = by_id[cid]
            results.append(RetrievedChunk(cid, ch.document_id, title, ch.section, ch.text, doc_type, ch.clearance_level,
                                          ch.tenant_id, round(score, 6), d_rank.get(cid), l_rank.get(cid)))
        latency = time.perf_counter() - t0
        m.RAG_LATENCY.observe(latency)
        m.RAG_RESULTS.observe(len(results))
        return results, {"latency_ms": round(latency * 1000, 2), "permitted_corpus": len(rows), "query_flags": flags, "mode": mode}

    @staticmethod
    def _rerank(query: str, fused: dict[str, float], by_id: dict) -> dict[str, float]:
        """Lightweight lexical reranker: boosts candidates whose section heading and body cover the query terms.
        Production: cross-encoder (bge-reranker-base) on the top-30 candidates."""
        q = set(tokens(query))
        if not q:
            return fused
        out = {}
        for cid, s in fused.items():
            ch = by_id[cid][0]
            body = set(tokens(ch.text))
            head = set(tokens(ch.section))
            coverage = len(q & body) / len(q)
            heading = len(q & head) / len(q)
            out[cid] = s + 0.02 * coverage + 0.01 * heading
        return out


def build_context(chunks: list[RetrievedChunk], max_words: int = 1400) -> str:
    """Citable, delimited context. Chunk text is untrusted data even when approved."""
    parts, used = [], 0
    for c in chunks:
        w = len(c.text.split())
        if used + w > max_words:
            break
        parts.append(f'<source chunk_id="{c.chunk_id}" title="{c.title}">\n{prompt_guard.neutralise(c.text)}\n</source>')
        used += w
    return "\n".join(parts)
