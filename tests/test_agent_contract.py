"""Citation validation + repair-loop tests (the anti-hallucination gate)."""

import pytest

from backend.agent.core import Agent
from backend.schemas import Citation


@pytest.fixture()
def seeded_db(tmp_db):
    tmp_db.add_document("d1", "policy_wording.md", "md", "Policy Wording")
    tmp_db.add_chunks(
        "d1",
        [
            {"section_path": "Free Look", "page": None, "text": "You may return the policy within fifteen days. Refunds apply."},
            {"section_path": "Grace Period", "page": None, "text": "Thirty days for annual mode."},
        ],
    )
    return tmp_db


@pytest.fixture()
def agent(seeded_db):
    class KB:
        def list_documents(self):
            return [{"doc_id": "d1", "filename": "policy_wording.md", "fmt": "md", "title": "Policy Wording"}]

    return Agent(llm=None, kb=KB(), retriever=None, search_tool=None, db=seeded_db, session_id="s1")


def test_valid_citation_accepted(agent):
    out = agent._validate_contract(
        {
            "answer": "Free look is 15 days.",
            "citations": [
                {"doc": "policy_wording.md", "section": "Free Look",
                 "span_text": "You may return the policy within fifteen days."}
            ],
        }
    )
    assert isinstance(out, object) and not isinstance(out, dict)
    assert out.citations_valid and out.citations[0].valid


def test_whitespace_and_punctuation_tolerance(agent):
    out = agent._validate_contract(
        {
            "answer": "x",
            "citations": [
                {"doc": "policy_wording.md",
                 "span_text": "You may   return the policy within  fifteen days."}
            ],
        }
    )
    assert not isinstance(out, dict)  # normalized match succeeds


def test_invalid_span_rejected_with_hints(agent):
    out = agent._validate_contract(
        {
            "answer": "x",
            "citations": [
                {"doc": "policy_wording.md", "section": "Free Look",
                 "span_text": "The free look window lasts 20 days per IRDAI norms."}
            ],
        }
    )
    assert isinstance(out, dict)  # repair hint, not an accepted answer
    assert out["invalid_citations"]
    assert "sections" in out["invalid_citations"][0]


def test_unknown_document_rejected(agent):
    out = agent._validate_contract(
        {"answer": "x", "citations": [{"doc": "made_up.pdf", "span_text": "some long enough span text"}]}
    )
    assert isinstance(out, dict)


def test_too_short_span_rejected(agent):
    out = agent._validate_contract(
        {"answer": "x", "citations": [{"doc": "policy_wording.md", "span_text": "x"}]}
    )
    assert isinstance(out, dict)


def test_accept_flagged_marks_invalid(agent):
    out = agent._accept_flagged(
        {
            "answer": "x",
            "citations": [{"doc": "policy_wording.md", "span_text": "fabricated span that never existed in any chunk"}],
        }
    )
    assert not out.citations[0].valid
    assert not out.citations_valid
    assert Citation.model_validate(out.citations[0].model_dump())