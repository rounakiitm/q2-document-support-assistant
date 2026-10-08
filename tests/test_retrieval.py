"""Retrieval tests: BM25 sparse path + RRF fusion."""

from backend.retrieval.retriever import HybridRetriever, _rrf


def _mk(text, doc="doc.md", section="S"):
    return {"text": text, "doc_id": "d1", "filename": doc, "section_path": section, "page": None}


class FakeKB:
    threshold = 0.35

    def __init__(self, chunks):
        self.chunks = chunks

    def bm25_search(self, query, k):
        toks = set(query.lower().split())
        out = []
        for c in self.chunks:
            score = len(toks & set(c["text"].lower().split()))
            if score:
                out.append((c, float(score)))
        out.sort(key=lambda p: p[1], reverse=True)
        return out[:k]


def test_bm25_ranking():
    kb = FakeKB([
        _mk("Grace period is thirty days for annual mode.", section="Grace"),
        _mk("The free look period is fifteen days.", section="Free Look"),
    ])
    out = HybridRetriever(kb).retrieve("grace period days", k=2)
    assert out["results"]
    assert "grace" in out["results"][0]["section_path"].lower()


def test_kb_hit_flag_respects_threshold():
    kb = FakeKB([_mk("totally unrelated content about orchids", section="Orchids")])
    out = HybridRetriever(kb).retrieve("grace period premium days", k=2)
    assert out["results"] == [] or not out["kb_hit"]


def test_rrf_fusion_merges_duplicates():
    dense = [(_mk("alpha beta gamma"), 0.9), (_mk("delta epsilon"), 0.7)]
    sparse = [(_mk("delta epsilon"), 3.0), (_mk("alpha beta gamma"), 1.0)]
    fused = _rrf(dense, sparse, k=4)
    assert len(fused) == 2  # no duplicates
    texts = [f["text"] for f in fused]
    assert "alpha beta gamma" in texts and "delta epsilon" in texts
    # dense best score is preserved for the kb_hit flag
    alpha = next(f for f in fused if f["text"] == "alpha beta gamma")
    assert alpha["score"] == 0.9


def test_rrf_prefers_items_in_both_lists():
    dense = [(_mk("only dense"), 0.5)]
    sparse = [(_mk("both lists here"), 1.0)]
    fused = _rrf(dense + [(_mk("both lists here"), 0.4)], sparse, k=4)
    assert fused[0]["text"] == "both lists here"  # appears in both lists -> higher rrf