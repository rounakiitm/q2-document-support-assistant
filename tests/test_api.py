"""End-to-end API tests with a scripted LLM (no network)."""

import pytest
from fastapi.testclient import TestClient

from backend import main as api_main
from backend.retrieval.retriever import HybridRetriever
from tests.conftest import FakeEmbeddings, FakeLLM

MD_CONTENT = """# Policy Wording

## Free Look
You may return the policy within fifteen days. Refunds apply.

## Grace Period
Thirty days for annual mode.
"""


class FakeKB:
    """KB with real loaders/storage but a fake vector index (BM25 only)."""

    threshold = 0.35

    def __init__(self, db):
        self.db = db
        self.embeddings = FakeEmbeddings()

    def list_documents(self):
        return self.db.get_documents()

    def ingest_file(self, path, filename):
        from backend.ingest.loader import fmt_for, load_any

        fmt = fmt_for(filename)
        docs = load_any(path, fmt)
        doc_id = "t" + filename[:6]
        self.db.add_document(doc_id, filename, fmt, filename)
        self.db.add_chunks(
            doc_id,
            [
                {
                    "section_path": d.metadata.get("section_path", ""),
                    "page": d.metadata.get("page"),
                    "text": d.page_content,
                }
                for d in docs
            ],
        )
        return {"doc_id": doc_id, "filename": filename, "fmt": fmt, "n_chunks": len(docs)}

    def delete(self, doc_id):
        self.db.delete_document(doc_id)

    def dense_search(self, query, k, doc_ids=None):
        return []

    def bm25_search(self, query, k, doc_ids=None):
        toks = set(query.lower().split())
        allow = set(doc_ids) if doc_ids else None
        out = []
        for c in self.db.get_chunks():
            if allow is not None and c.get("doc_id") not in allow:
                continue
            score = len(toks & set(c["text"].lower().split()))
            if score:
                out.append((c, float(score)))
        out.sort(key=lambda p: p[1], reverse=True)
        return out[:k]

    def rebuild_bm25(self):
        pass


@pytest.fixture()
def client(monkeypatch):
    kb = FakeKB(api_main.db)
    fake = FakeLLM()
    monkeypatch.setattr(api_main, "_kb", kb)
    monkeypatch.setattr(api_main, "_retriever", HybridRetriever(kb))
    monkeypatch.setattr(api_main, "llm", fake)
    from backend.tools.search_tool import SearchTool

    monkeypatch.setattr(api_main, "search", SearchTool(fake))
    monkeypatch.setattr(api_main, "_embedder", lambda: FakeEmbeddings().embed_query)
    from fastapi.testclient import TestClient

    return TestClient(api_main.app), fake, kb


def test_health(client):
    c, _, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ingest_list_delete(client):
    c, _, _ = client
    r = c.post(
        "/documents",
        files={"file": ("policy.md", MD_CONTENT.encode(), "text/markdown")},
    )
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["fmt"] == "md" and info["n_chunks"] >= 2
    docs = c.get("/documents").json()
    assert any(d["filename"] == "policy.md" for d in docs)
    r = c.delete(f"/documents/{info['doc_id']}")
    assert r.status_code == 200
    assert not any(d["filename"] == "policy.md" for d in c.get("/documents").json())


def test_chat_turn_with_valid_citation(client):
    c, fake, kb = client
    c.post("/documents", files={"file": ("policy.md", MD_CONTENT.encode(), "text/markdown")})
    sid = c.post("/sessions").json()["session_id"]
    fake.script = [
        {"calls": [{"name": "retrieve", "args": {"query": "free look period"}}]},
        {
            "calls": [
                {
                    "name": "submit_answer",
                    "args": {
                        "answer": "The free look period is fifteen days.",
                        "citations": [
                            {
                                "doc": "policy.md",
                                "section": "Free Look",
                                "span_text": "You may return the policy within fifteen days.",
                            }
                        ],
                    },
                }
            ]
        },
    ]
    r = c.post(f"/sessions/{sid}/messages", params={"stream": "false"}, json={"question": "What is the free look period?"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert "fifteen days" in answer["answer"]
    assert answer["citations_valid"] and answer["citations"][0]["valid"]
    assert answer["memory"]["intent"] == "kb_lookup"
    assert answer["turn"] == 1
    # session memory endpoint reflects the asked-question log
    mem = c.get(f"/sessions/{sid}/memory").json()
    assert len(mem["asked_questions"]) == 1


def test_chat_turn_rejects_then_repairs_citation(client):
    c, fake, kb = client
    c.post("/documents", files={"file": ("policy.md", MD_CONTENT.encode(), "text/markdown")})
    sid = c.post("/sessions").json()["session_id"]
    fake.script = [
        {"calls": [{"name": "retrieve", "args": {"query": "grace period"}}]},
        {
            "calls": [
                {
                    "name": "submit_answer",
                    "args": {
                        "answer": "Grace is 30 days.",
                        "citations": [{"doc": "policy.md", "span_text": "Fabricated span that never existed anywhere."}],
                    },
                }
            ]
        },
        {
            "calls": [
                {
                    "name": "submit_answer",
                    "args": {
                        "answer": "Grace period is thirty days for annual mode.",
                        "citations": [
                            {"doc": "policy.md", "section": "Grace Period", "span_text": "Thirty days for annual mode."}
                        ],
                    },
                }
            ]
        },
    ]
    r = c.post(f"/sessions/{sid}/messages", params={"stream": "false"}, json={"question": "What is the grace period?"})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["citations_valid"] and answer["citations"][0]["valid"]
    trace = [run["tool"] for run in answer["tool_runs"]]
    assert "retrieve" in trace


def test_sse_stream_events(client):
    c, fake, kb = client
    c.post("/documents", files={"file": ("policy.md", MD_CONTENT.encode(), "text/markdown")})
    sid = c.post("/sessions").json()["session_id"]
    fake.script = [
        {"calls": [{"name": "retrieve", "args": {"query": "free look"}}]},
        {
            "calls": [
                {
                    "name": "submit_answer",
                    "args": {
                        "answer": "Fifteen days.",
                        "citations": [
                            {
                                "doc": "policy.md",
                                "section": "Free Look",
                                "span_text": "You may return the policy within fifteen days.",
                            }
                        ],
                    },
                }
            ]
        },
    ]
    r = c.post(
        f"/sessions/{sid}/messages",
        params={"stream": "true"},
        json={"question": "What is the free look period?"},
    )
    assert r.status_code == 200
    body = r.text
    assert "event: memory" in body
    assert "event: tool_call" in body
    assert "event: tool_result" in body
    assert "event: answer" in body