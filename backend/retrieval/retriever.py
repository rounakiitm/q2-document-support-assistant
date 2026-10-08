"""Hybrid retrieval: dense (Chroma) + sparse (BM25) fused by reciprocal rank."""

from __future__ import annotations

import logging

log = logging.getLogger("retrieval")


class HybridRetriever:
    def __init__(self, kb):
        self.kb = kb

    def retrieve(
        self, query: str, k: int = 6, doc_ids: list[str] | None = None
    ) -> dict:
        dense = self._dense(query, k, doc_ids=doc_ids)
        sparse = self.kb.bm25_search(query, k, doc_ids=doc_ids)
        fused = _rrf(dense, sparse, k)
        scope = "document" if doc_ids else "all"
        if not fused:
            return {
                "results": [],
                "best_score": 0.0,
                "kb_hit": False,
                "scope": scope,
                "doc_ids": list(doc_ids or []),
            }
        best_score = fused[0]["score"]
        return {
            "results": fused,
            "best_score": round(best_score, 4),
            "kb_hit": best_score >= self.kb.threshold,
            "scope": scope,
            "doc_ids": list(doc_ids or []),
        }

    def _dense(
        self, query: str, k: int, doc_ids: list[str] | None = None
    ) -> list[tuple[dict, float]]:
        try:
            hits = self.kb.dense_search(query, k, doc_ids=doc_ids)
        except Exception:  # noqa: BLE001 - empty index or embedding failure
            log.exception("dense retrieval failed")
            return []
        out = []
        for doc, distance in hits:
            meta = doc.metadata or {}
            chunk = {
                "text": doc.page_content,
                "doc_id": meta.get("doc_id", ""),
                "filename": meta.get("filename", ""),
                "section_path": meta.get("section_path", ""),
                "page": meta.get("page"),
            }
            out.append((chunk, 1.0 - float(distance)))  # cosine distance -> similarity
        return out


def _rrf(dense, sparse, k: int, k_const: int = 60) -> list[dict]:
    """Reciprocal-rank fusion over both lists; reports the best dense score."""
    scores: dict[str, float] = {}
    best_dense: dict[str, float] = {}
    for rank, (chunk, score) in enumerate(dense):
        key = chunk["text"][:200]
        scores[key] = scores.get(key, 0.0) + 1.0 / (k_const + rank + 1)
        best_dense[key] = max(best_dense.get(key, 0.0), score)
    for rank, (chunk, _score) in enumerate(sparse):
        key = chunk["text"][:200]
        scores[key] = scores.get(key, 0.0) + 1.0 / (k_const + rank + 1)
        best_dense.setdefault(key, 0.0)

    seen: set[str] = set()
    out: list[dict] = []
    for chunk, score in dense + sparse:
        key = chunk["text"][:200]
        if key in seen:
            continue
        seen.add(key)
        out.append({**chunk, "rrf": round(scores[key], 5), "score": best_dense[key]})
        if len(out) >= k:
            break
    out.sort(key=lambda c: c["rrf"], reverse=True)
    return out