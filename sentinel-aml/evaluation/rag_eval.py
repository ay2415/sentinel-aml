"""Retrieval evaluation: Recall@K, Precision@K, MRR, nDCG@K, Hit@K per retrieval mode, plus ACL leakage audit."""
from __future__ import annotations

import math
import time

import yaml

from common import ROOT, pct, write_report  # noqa: I001

from sqlalchemy import select  # noqa: E402

from app.database import session  # noqa: E402
from app.database.models import KBChunk  # noqa: E402
from app.rag.retriever import AccessContext, Retriever  # noqa: E402

K = 5
MODES = ["dense", "lexical", "hybrid", "hybrid_rerank"]


def metrics(ranked: list[str], relevant: set[str], k: int = K) -> dict:
    top = ranked[:k]
    hits = [1 if c in relevant else 0 for c in top]
    rr = next((1 / (i + 1) for i, c in enumerate(ranked) if c in relevant), 0.0)
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return {"recall": sum(hits) / len(relevant), "precision": sum(hits) / k, "mrr": rr, "ndcg": dcg / idcg if idcg else 0.0,
            "hit": 1.0 if any(hits) else 0.0}


def main() -> None:
    qs = yaml.safe_load((ROOT / "evaluation" / "datasets" / "rag_queries.yaml").read_text())
    session.configure()
    r = Retriever()
    results, per_query = {}, []
    with session.session_scope() as db:
        access = {(c.id): (c.tenant_id, c.clearance_level) for c in db.execute(select(KBChunk).where(KBChunk.is_active)).scalars()}
        for mode in MODES:
            agg, lat = {"recall": [], "precision": [], "mrr": [], "ndcg": [], "hit": []}, []
            for q in qs:
                ctx = AccessContext(q["tenant"], q["clearance"])
                rel = {c for c in q["relevant"] if c in access and access[c][0] in (None, q["tenant"]) and access[c][1] <= q["clearance"]}
                t = time.perf_counter()
                res, _ = r.search(db, q["query"], ctx, top_k=10, mode=mode)
                lat.append((time.perf_counter() - t) * 1000)
                m = metrics([x.chunk_id for x in res], rel)
                for k_, v in m.items():
                    agg[k_].append(v)
                if mode == "hybrid_rerank":
                    per_query.append({"id": q["id"], **{k_: round(v, 3) for k_, v in m.items()}, "top3": [x.chunk_id for x in res[:3]]})
            results[mode] = {f"{k_}@{K}" if k_ != "mrr" else "mrr": round(sum(v) / len(v), 4) for k_, v in agg.items()}
            results[mode].update({"latency_p50_ms": round(pct(lat, 50), 2), "latency_p95_ms": round(pct(lat, 95), 2)})

        # ACL leakage audit: every query, every role clearance, both tenants -> count unauthorised chunks returned
        leaks, checked = 0, 0
        for q in qs:
            for tenant in ("emerald", "liffey"):
                for clearance in (0, 1, 2, 3):
                    for mode in MODES:
                        res, _ = r.search(db, q["query"], AccessContext(tenant, clearance), top_k=20, mode=mode)
                        for x in res:
                            checked += 1
                            if x.tenant_id not in (None, tenant) or x.classification_level > clearance:
                                leaks += 1
    report = {"k": K, "queries": len(qs), "embedding_model": "glove-wiki-gigaword-300-sif (local)", "reranker": "lexical-coverage (local)",
              "modes": results, "acl_audit": {"results_checked": checked, "unauthorised_results": leaks}, "per_query_hybrid_rerank": per_query,
              "caveat": "Single-annotator labels on a 62-chunk synthetic corpus; indicative only."}
    path = write_report("rag_eval", report)
    print(f"wrote {path}")
    for mode, v in results.items():
        print(f"{mode:14s} {v}")
    print("ACL audit:", report["acl_audit"])


if __name__ == "__main__":
    main()
