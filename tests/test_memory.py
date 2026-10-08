"""Memory layer tests: repeat detection, topic ledger, compaction."""

from backend.memory.memory import SessionMemory
from tests.conftest import FakeEmbeddings, FakeLLM


def test_pre_turn_fresh_session(tmp_db):
    llm = FakeLLM(json_responses=[{
        "rewritten_query": "free look period",
        "intent": "kb_lookup",
        "topics": ["free look"],
        "topic_switch": False,
        "repeat_of_turn": 0,
    }])
    mem = SessionMemory(tmp_db, llm, "s1", embedder=FakeEmbeddings().embed_query)
    pre = mem.pre_turn("What is the free look period?")
    assert pre["rewritten_query"] == "free look period"
    assert pre["intent"] == "kb_lookup"
    assert not pre["likely_repeat"]
    assert "TOPICS DISCUSSED" in pre["memory_card"]


def test_post_turn_updates_ledger(tmp_db):
    llm = FakeLLM(json_responses=[{"topics": ["free look"], "intent": "kb_lookup", "rewritten_query": "q1", "repeat_of_turn": 0}])
    mem = SessionMemory(tmp_db, llm, "s1", embedder=FakeEmbeddings().embed_query)
    pre = mem.pre_turn("What is the free look period?")
    mem.post_turn("What is the free look period?", "It is 15 days.", pre)
    topics = tmp_db.get_topics("s1")
    assert topics and topics[0]["topic"] == "free look"
    asked = tmp_db.get_asked_questions("s1")
    assert len(asked) == 1 and asked[0]["turn"] == 1
    # second turn on the same topic extends last_turn
    llm.json_responses = [{"topics": ["free look"], "intent": "follow_up", "rewritten_query": "q2", "repeat_of_turn": 0}]
    pre2 = mem.pre_turn("What about refunds in that window?")
    mem.post_turn("What about refunds in that window?", "Refund less risk premium.", pre2)
    topics = tmp_db.get_topics("s1")
    assert len(topics) == 1 and topics[0]["last_turn"] == 2


def test_repeat_detected_across_rephrase(tmp_db):
    # identical question text -> identical fake embedding -> deterministic repeat
    llm = FakeLLM(json_responses=[
        {"topics": ["free look"], "intent": "kb_lookup", "rewritten_query": "q", "repeat_of_turn": 0},
        # second pre-pass: LLM says no repeat, but embedding agreement forces it
        {"topics": ["free look"], "intent": "kb_lookup", "rewritten_query": "q again", "repeat_of_turn": 0},
    ])
    mem = SessionMemory(tmp_db, llm, "s1", embedder=FakeEmbeddings().embed_query)
    pre1 = mem.pre_turn("What is the free look period for term plans?")
    mem.post_turn("What is the free look period for term plans?", "15 days.", pre1)
    pre2 = mem.pre_turn("What is the free look period for term plans?")
    assert pre2["likely_repeat"] and pre2["repeat_of_turn"] == 1


def test_llm_failure_degrades_gracefully(tmp_db):
    class BrokenLLM(FakeLLM):
        def json_call(self, *a, **k):
            raise RuntimeError("api down")

    mem = SessionMemory(tmp_db, BrokenLLM(), "s1", embedder=None)
    pre = mem.pre_turn("What is the grace period?")
    assert pre["rewritten_query"] == "What is the grace period?"
    assert pre["intent"] == "kb_lookup"


def test_compaction_updates_summary(tmp_db, monkeypatch):
    from backend import config

    monkeypatch.setattr(config.settings, "summary_after_turns", 2)
    sid = tmp_db.create_session()
    llm = FakeLLM(json_responses=[{"summary": "Session covered the free look period."}])
    mem = SessionMemory(tmp_db, llm, sid, embedder=None)
    for i in range(5):  # 10 messages; keep last 8 -> 2 compacted
        tmp_db.add_message(sid, "user", f"question {i} about free look?")
        tmp_db.add_message(sid, "assistant", f"answer {i}: 15 days apply.")
    mem._maybe_compact()
    assert tmp_db.get_summary(sid) == "Session covered the free look period."