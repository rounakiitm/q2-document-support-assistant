"""Conversation memory layer: rewrite + intent + repeat detection + topic ledger.

Per session, persisted in SQLite:
- topics ledger (topic -> first/last turn)
- asked-question log (question, intent, topics, answer summary, embedding)
- rolling summary of older turns (compaction for long sessions)

`pre_turn` runs one cheap structured LLM call BEFORE the agent loop and emits
a memory card + rewritten query. `post_turn` updates the ledger after an
answer lands. Repeat detection is dual: cosine similarity on question
embeddings (deterministic) + LLM judgement (semantic), so "avoid repeating"
is measurable, not vibes.
"""

from __future__ import annotations

import json
import logging
import math

from backend.config import settings
from backend.storage.db import DB

log = logging.getLogger("memory")

PRE_SYSTEM = """You are the memory controller of a document-support assistant.
Given the conversation memory and the new user question, return JSON:
{
 "rewritten_query": "self-contained search query (resolve pronouns/references
   like 'that', 'it' against the topics; if no resolution needed, echo the
   question)",
 "intent": "kb_lookup" | "follow_up" | "meta_about_session" | "chitchat",
 "topics": ["1-3 short topic labels this question is about"],
 "topic_switch": true if this question changes to a topic unlike the recent
   conversation, else false,
 "repeat_of_turn": 0 if new, else the turn number this repeats/rephrases
}
Rules: repeat_of_turn must be a prior turn from the asked-question log whose
answer already covers this question (rephrasing counts). meta_about_session
means the user asks about the conversation itself ("what have we covered?")."""


class SessionMemory:
    def __init__(self, db: DB, llm, sid: str, embedder=None):
        self.db = db
        self.llm = llm
        self.sid = sid
        self.embedder = embedder  # callable: str -> list[float]

    # ------------------------------------------------------------------ turn
    @property
    def turn(self) -> int:
        return len(self.db.get_asked_questions(self.sid)) + 1

    def pre_turn(self, question: str) -> dict:
        topics = self.db.get_topics(self.sid)
        asked = self.db.get_asked_questions(self.sid)
        summary = self.db.get_summary(self.sid)

        # deterministic repeat detection on question embeddings
        embedding = ""
        matched_turn, match_sim = 0, 0.0
        if self.embedder:
            try:
                vec = self.embedder(question)
                embedding = json.dumps(vec)
                for prior in self.db.get_asked_questions(self.sid):
                    if not prior["question_embedding"]:
                        continue
                    sim = _cosine(vec, json.loads(prior["question_embedding"]))
                    if sim > match_sim:
                        match_sim, matched_turn = sim, prior["turn"]
            except Exception:  # noqa: BLE001 - embedding failure must not block chat
                log.exception("question embedding failed")

        embed_repeat = matched_turn and match_sim >= settings.repeat_similarity_threshold

        pre = self._llm_pass(question, topics, asked, summary, matched_turn)
        pre["question_embedding"] = embedding
        pre["embed_repeat"] = embed_repeat
        pre["embed_match_turn"] = matched_turn
        pre["embed_similarity"] = round(match_sim, 4)
        # semantic + embedding agreement -> high confidence repeat
        if embed_repeat and pre.get("repeat_of_turn", 0) in (0, matched_turn):
            pre["repeat_of_turn"] = matched_turn
        elif embed_repeat and not pre.get("repeat_of_turn"):
            pre["repeat_of_turn"] = matched_turn
        pre["likely_repeat"] = bool(pre.get("repeat_of_turn"))
        pre["memory_card"] = self._memory_card(topics, asked, summary)
        return pre

    def _llm_pass(self, question, topics, asked, summary, matched_turn) -> dict:
        try:
            payload = {
                "topic_ledger": topics[-8:],
                "asked_questions": [
                    {"turn": a["turn"], "question": a["question"], "intent": a["intent"]}
                    for a in asked[-8:]
                ],
                "rolling_summary": summary[:800],
                "embedding_suggested_repeat_of_turn": matched_turn or None,
                "new_question": question,
            }
            out = self.llm.json_call(json.dumps(payload, default=str), PRE_SYSTEM)
        except Exception:  # noqa: BLE001 - degrade to deterministic behavior
            log.exception("memory pre-pass failed")
            out = {}
        return {
            "rewritten_query": str(out.get("rewritten_query") or question),
            "intent": str(out.get("intent") or "kb_lookup"),
            "topics": [str(t)[:40] for t in (out.get("topics") or [])][:3],
            "topic_switch": bool(out.get("topic_switch")),
            "repeat_of_turn": int(out.get("repeat_of_turn") or 0),
        }

    @staticmethod
    def _memory_card(topics, asked, summary) -> str:
        lines = ["TOPICS DISCUSSED SO FAR:"]
        if topics:
            lines.append(
                ", ".join(f"{t['topic']} (turn {t['first_turn']})" for t in topics)
            )
        else:
            lines.append("(none yet)")
        lines.append("QUESTIONS ALREADY ANSWERED:")
        if asked:
            for a in asked:
                lines.append(f"- turn {a['turn']} [{a['intent']}]: {a['question']}")
        else:
            lines.append("(none yet)")
        if summary:
            lines.append("SUMMARY OF OLDER TURNS:\n" + summary)
        return "\n".join(lines)

    # ------------------------------------------------------------------ post
    def post_turn(self, question: str, answer_text: str, pre: dict) -> None:
        turn = self.turn
        for topic in pre.get("topics") or []:
            if topic:
                self.db.upsert_topic(self.sid, topic, turn)
        self.db.add_asked_question(
            self.sid,
            turn,
            question,
            pre.get("intent", "kb_lookup"),
            pre.get("topics") or [],
            answer_text[:400],
            pre.get("question_embedding", ""),
        )
        self._maybe_compact()

    def _maybe_compact(self) -> None:
        n_msgs = self.db.count_messages(self.sid)
        if n_msgs < settings.summary_after_turns:
            return
        msgs = self.db.get_messages(self.sid)
        keep = 8  # recent turns stay verbatim in history
        older = msgs[:-keep]
        if not older:
            return
        prior = self.db.get_summary(self.sid)
        transcript = "\n".join(
            f"{'USER' if m['role'] == 'user' else 'ASSISTANT'}: {m['content'][:400]}"
            for m in older
        )
        try:
            out = self.llm.json_call(
                f"PRIOR SUMMARY:\n{prior or '(none)'}\n\nOLDER TURNS:\n{transcript}",
                "Merge the prior summary and the older turns into one compact"
                " rolling summary (max 180 words) preserving topics, questions"
                " asked and key facts. Return JSON: {\"summary\": \"...\"}",
            )
            summary = str(out.get("summary") or "").strip()
            if summary:
                self.db.set_summary(self.sid, summary)
        except Exception:  # noqa: BLE001 - compaction is best-effort
            log.exception("summary compaction failed")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0