from backend.mentions import mention_alias, parse_mentions, resolve_mentions


def test_mention_alias():
    assert mention_alias("policy_wording.pdf") == "policy_wording"
    assert mention_alias("Free Look Rules.md") == "Free_Look_Rules"


def test_parse_and_resolve():
    docs = [
        {"doc_id": "a1", "filename": "policy_wording.pdf", "fmt": "pdf"},
        {"doc_id": "b2", "filename": "claims_procedure.md", "fmt": "md"},
    ]
    q = "@policy_wording what is the free look period?"
    assert parse_mentions(q) == ["policy_wording"]
    out = resolve_mentions(q, docs)
    assert out["scope"] == "document"
    assert out["focus_doc_ids"] == ["a1"]
    assert "free look" in out["clean_question"].lower()
    assert "@" not in out["clean_question"]


def test_no_mention_searches_all():
    docs = [{"doc_id": "a1", "filename": "policy_wording.pdf", "fmt": "pdf"}]
    out = resolve_mentions("what is grace period?", docs)
    assert out["scope"] == "all"
    assert out["focus_doc_ids"] == []
