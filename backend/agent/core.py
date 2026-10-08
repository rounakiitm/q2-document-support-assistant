"""Agent core: Gemini function-calling loop with citation validation + repair.

Every final answer carries citations[{doc, section, span_text}]. Each span is
string-matched against the actual KB chunks before the answer is accepted;
invalid citations are returned to the model with hints (max 2 repairs), after
which the answer is accepted but flagged citations_valid=false. This is the
anti-hallucination gate: a citation can only exist if the text genuinely
made it into the index.
"""

import json
import logging
import re
import time

from google.genai import types

from backend.agent.prompts import TOOL_DECLS, system_prompt
from backend.config import settings
from backend.schemas import AnswerOut, Citation, Source

log = logging.getLogger("agent")

SUBMIT = "submit_answer"
NUDGE = (
    "Deliver your final answer now using the submit_answer tool, in the"
    " required format with citations copied verbatim from retrieved chunks."
)
WEB_SENTINEL = "outside the knowledge base"


def _normalize(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).casefold().strip()


class Agent:
    def __init__(self, llm, kb, retriever, search_tool, db, session_id, on_event=None):
        self.llm = llm
        self.kb = kb
        self.retriever = retriever
        self.search = search_tool
        self.db = db
        self.sid = session_id
        self.on_event = on_event or (lambda event, data: None)
        self.focus_doc_ids: list[str] = []
        self.focus_docs: list[dict] = []

    # ------------------------------------------------------------------ main
    def ask(self, question: str, pre: dict, history: list | None = None) -> AnswerOut:
        self.focus_docs = list(pre.get("focus_docs") or [])
        self.focus_doc_ids = list(pre.get("focus_doc_ids") or [])
        contents = list(history or [])
        contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

        config = types.GenerateContentConfig(
            system_instruction=self._system(pre),
            tools=[types.Tool(function_declarations=TOOL_DECLS)],
            temperature=0.1,
        )

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}
        answer: AnswerOut | None = None
        nudged = False
        repair_attempts = 0

        for _step in range(settings.agent_max_iterations):
            resp = self.llm.generate(contents, config)
            usage_meta = getattr(resp, "usage_metadata", None)
            if usage_meta:
                usage["prompt_tokens"] += usage_meta.prompt_token_count or 0
                usage["completion_tokens"] += usage_meta.candidates_token_count or 0
                usage["llm_calls"] += 1
                self.db.add_usage(
                    self.sid,
                    usage_meta.prompt_token_count or 0,
                    usage_meta.candidates_token_count or 0,
                )

            candidates = getattr(resp, "candidates", None)
            calls: list[types.FunctionCall] = []
            if candidates and candidates[0].content and candidates[0].content.parts:
                calls = [
                    part.function_call
                    for part in candidates[0].content.parts
                    if part and part.function_call
                ]

            if not calls:
                text = resp.text or ""
                contents.append(types.Content(role="model", parts=[types.Part(text=text)]))
                if nudged:
                    answer = self._fallback(text)
                    break
                nudged = True
                contents.append(types.Content(role="user", parts=[types.Part(text=NUDGE)]))
                continue

            contents.append(candidates[0].content)
            submitted = False
            for call in calls:
                if call.name == SUBMIT:
                    outcome = self._validate_contract(dict(call.args or {}))
                    if isinstance(outcome, AnswerOut):
                        answer = outcome
                        submitted = True
                        contents.append(self._fn_response(SUBMIT, {"ok": True}))
                    elif outcome is None:
                        contents.append(
                            self._fn_response(
                                SUBMIT,
                                {"ok": False, "error": "answer field missing or empty"},
                            )
                        )
                    else:
                        # outcome is a repair hint dict
                        repair_attempts += 1
                        if repair_attempts > settings.citation_repair_attempts:
                            answer = self._accept_flagged(dict(call.args or {}))
                            submitted = True
                            contents.append(
                                self._fn_response(
                                    SUBMIT,
                                    {
                                        "ok": True,
                                        "note": "accepted with citations flagged invalid",
                                    },
                                )
                            )
                        else:
                            contents.append(self._fn_response(SUBMIT, outcome))
                    continue
                result = self._dispatch(call.name, dict(call.args or {}))
                contents.append(self._fn_response(call.name, result))

            if submitted:
                break

        if answer is None:
            answer = AnswerOut(
                session_id=self.sid,
                answer=(
                    "I couldn't complete this within my working limits"
                    f" ({settings.agent_max_iterations} reasoning steps) and I don't"
                    " guess. Try rephrasing, or ask about a section of a specific"
                    " document."
                ),
                assumptions=["agent hit its iteration cap; no answer fabricated"],
            )

        answer.turn = self.db.count_messages(self.sid) // 2 + 1
        answer.tool_runs = self.db.get_tool_runs(self.sid, limit=20)
        answer.usage = usage
        answer.memory = {
            "intent": pre.get("intent"),
            "rewritten_query": pre.get("rewritten_query"),
            "topics": pre.get("topics"),
            "topic_switch": bool(pre.get("topic_switch")),
            "likely_repeat": bool(pre.get("likely_repeat")),
            "repeat_of_turn": int(pre.get("repeat_of_turn") or 0),
        }
        self.db.add_message(
            self.sid,
            "assistant",
            answer.answer,
            meta={
                "citations": [c.model_dump() for c in answer.citations],
                "citations_valid": answer.citations_valid,
                "not_in_kb": answer.not_in_kb,
                "compressed": answer.compressed,
                "topic_switch_ack": answer.topic_switch_ack,
                "memory": answer.memory,
                "web_sources": [s.model_dump() for s in answer.web_sources],
                "usage": usage,
                "tool_runs": answer.tool_runs,
                "focus_docs": self.focus_docs,
            },
        )
        return answer

    # ------------------------------------------------------------------ tools
    def _system(self, pre: dict) -> str:
        from backend.agent.prompts import kb_card, system_prompt

        docs = self.kb.list_documents()
        by_doc: dict[str, list[str]] = {}
        for ch in self.db.get_chunks():
            by_doc.setdefault(ch["doc_id"], []).append(ch["section_path"])
        card = kb_card(docs, by_doc)
        return system_prompt(card, pre.get("memory_card", ""), pre)

    def _dispatch(self, name: str, args: dict) -> dict:
        label = {
            "retrieve": "Searching knowledge base",
            "fetch_section": "Reading document section",
            "web_search": "Web lookup (outside KB)",
        }.get(name, name)
        self.on_event(
            "tool_call",
            {
                "name": name,
                "label": label,
                "args": args,
                "preview": self._args_preview(name, args),
            },
        )
        t0 = time.time()
        try:
            if name == "retrieve":
                result = self._retrieve(args)
            elif name == "fetch_section":
                result = self._fetch_section(args)
            elif name == "web_search":
                result = self.search.search(args.get("query", ""))
                for s in result.get("sources", []):
                    s["outside_kb"] = True
            else:
                result = {"ok": False, "error": f"unknown tool: {name}"}
        except Exception as exc:
            log.exception("tool %s crashed", name)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        result["latency_ms"] = round((time.time() - t0) * 1000, 1)
        self.db.add_tool_run(
            self.sid, name, json.dumps(args, default=str), json.dumps(result, default=str),
            bool(result.get("ok")), result["latency_ms"],
        )
        summary = self._summarize(result)
        self.on_event(
            "tool_result",
            {
                "name": name,
                "label": label,
                "ok": bool(result.get("ok")),
                "latency_ms": result["latency_ms"],
                "summary": summary,
                "preview": self._args_preview(name, args),
                "detail": self._result_detail(name, result),
            },
        )
        return result

    def _retrieve(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "empty query"}
        k = min(int(args.get("k") or settings.retrieval_k), 12)
        doc_ids = list(self.focus_doc_ids)
        doc_arg = str(args.get("doc") or "").strip().lstrip("@")
        if doc_arg:
            matched = self._match_doc(_normalize(doc_arg))
            if matched:
                doc_ids = [matched["doc_id"]]
            else:
                return {
                    "ok": False,
                    "error": f"unknown document '{doc_arg}'",
                    "documents": [d["filename"] for d in self.kb.list_documents()],
                }
        out = self.retriever.retrieve(query, k, doc_ids=doc_ids or None)
        for r in out["results"]:
            r["text"] = r["text"][:1500]
        out["ok"] = True
        if doc_ids:
            out["scoped_to"] = [
                d.get("filename") or d.get("doc_id")
                for d in (self.focus_docs or [])
                if d.get("doc_id") in doc_ids
            ] or doc_ids
        if not out["kb_hit"]:
            out["note"] = (
                "best score below the KB threshold - this may not be covered"
                " by the knowledge base; be honest if nothing relevant appears"
            )
        return out

    def _fetch_section(self, args: dict) -> dict:
        doc_q = _normalize(str(args.get("doc") or ""))
        sec_q = _normalize(str(args.get("section") or ""))
        if not doc_q:
            return {"ok": False, "error": "doc required"}
        doc = self._match_doc(doc_q)
        if not doc:
            names = [d["filename"] for d in self.kb.list_documents()]
            return {"ok": False, "error": "no such document", "documents": names}
        chunks = [
            c for c in self.db.get_chunks(doc["doc_id"])
            if not sec_q or sec_q in _normalize(c["section_path"]) or sec_q in _normalize(c["text"])
        ]
        if not chunks:
            sections = sorted({c["section_path"] for c in self.db.get_chunks(doc["doc_id"]) if c["section_path"]})
            return {"ok": False, "error": "no matching section", "sections": sections[:20]}
        text = "\n\n".join(
            (f"[{c['section_path']}] " if c["section_path"] else "") + c["text"]
            for c in chunks
        )[:4000]
        return {"ok": True, "doc": doc["filename"], "text": text}

    def _match_doc(self, doc_q: str) -> dict | None:
        for d in self.kb.list_documents():
            for candidate in (d["filename"], d["doc_id"], d.get("title", "")):
                if candidate and (doc_q in _normalize(candidate) or _normalize(candidate) in doc_q):
                    return d
        return None

    # ------------------------------------------------------------- contract
    def _validate_contract(self, args: dict) -> dict | AnswerOut | None:
        text = str(args.get("answer") or "").strip()
        if not text:
            return None
        raw_citations = args.get("citations") or []
        if not isinstance(raw_citations, list):
            raw_citations = []
        if not raw_citations and not bool(args.get("not_in_kb")):
            return {
                "ok": False,
                "error": (
                    "Answer draws on KB content but citations is empty."
                    " Add citations (doc + verbatim span_text from chunks you"
                    " received via retrieve/fetch_section), or set not_in_kb=true"
                    " only if the KB does not cover this question."
                ),
            }
        invalid = [c for c in raw_citations if not self._citation_ok(c)]
        answer = AnswerOut(
            session_id=self.sid,
            answer=text,
            citations=[self._to_citation(c) for c in raw_citations],
            not_in_kb=bool(args.get("not_in_kb")),
            compressed=bool(args.get("compressed")),
            topic_switch_ack=bool(args.get("topic_switch_ack")),
            assumptions=[str(a) for a in (args.get("assumptions") or [])],
        )
        if invalid:
            hints = self._repair_hints(invalid)
            log.info("citation validation failed: %d invalid", len(invalid))
            return {
                "ok": False,
                "error": (
                    f"{len(invalid)} of {len(raw_citations)} citations failed"
                    " validation: span_text must be a VERBATIM substring of a"
                    " chunk you received from retrieve/fetch_section."
                ),
                "invalid_citations": hints,
                "fix": "copy the span exactly from tool output, or drop the citation",
            }
        answer.citations_valid = True
        return answer

    def _accept_flagged(self, args: dict) -> AnswerOut:
        raw = args.get("citations") or []
        answer = AnswerOut(
            session_id=self.sid,
            answer=str(args.get("answer") or "").strip(),
            citations=[self._to_citation(c) for c in raw],
            not_in_kb=bool(args.get("not_in_kb")),
            compressed=bool(args.get("compressed")),
            topic_switch_ack=bool(args.get("topic_switch_ack")),
            assumptions=[str(a) for a in (args.get("assumptions") or [])],
        )
        for c in answer.citations:
            c.valid = self._citation_ok(c.model_dump())
        answer.citations_valid = all(c.valid for c in answer.citations) if answer.citations else True
        answer.assumptions = answer.assumptions + [
            "citation validation failed after repair attempts; spans flagged"
        ]
        return answer

    def _to_citation(self, c) -> Citation:
        if isinstance(c, dict):
            return Citation(
                doc=str(c.get("doc") or ""),
                section=str(c.get("section") or ""),
                span_text=str(c.get("span_text") or ""),
                valid=True,
            )
        return Citation(doc=str(c), span_text="", valid=True)

    def _citation_ok(self, c: dict) -> bool:
        if not isinstance(c, dict) or not str(c.get("span_text") or "").strip():
            return False
        span = _normalize(str(c.get("span_text")))
        if len(span) < 8:
            return False
        doc = self._match_doc(_normalize(str(c.get("doc") or "")))
        if doc is None:
            return False
        chunks = self.db.get_chunks(doc["doc_id"])
        sec = _normalize(str(c.get("section") or ""))
        for ch in chunks:
            if sec and sec not in _normalize(ch["section_path"]):
                continue
            if span in _normalize(ch["text"]):
                return True
        return False

    def _repair_hints(self, invalid: list) -> list[dict]:
        hints = []
        for c in invalid:
            if not isinstance(c, dict):
                continue
            doc = self._match_doc(_normalize(str(c.get("doc") or "")))
            entry = {"doc": str(c.get("doc") or ""), "span_text": str(c.get("span_text") or "")[:200]}
            if doc:
                chunks = self.db.get_chunks(doc["doc_id"])
                entry["sections"] = sorted({ch["section_path"] for ch in chunks if ch["section_path"]})[:12]
                # offer the chunk with the highest rough overlap as a re-anchor
                span = _normalize(str(c.get("span_text") or ""))
                words = set(span.split())
                best, best_overlap = None, 0.0
                for ch in chunks:
                    cw = set(_normalize(ch["text"]).split())
                    if cw:
                        ov = len(words & cw) / max(len(words), 1)
                        if ov > best_overlap:
                            best, best_overlap = ch, ov
                if best:
                    entry["closest_chunk"] = {
                        "doc": doc["filename"],
                        "section": best["section_path"],
                        "text_start": best["text"][:300],
                    }
            hints.append(entry)
        return hints

    # ------------------------------------------------------------------ util
    @staticmethod
    def _fn_response(name: str, response: dict) -> types.Content:
        return types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name, response=response
                    )
                )
            ],
        )

    @staticmethod
    def _args_preview(name: str, args: dict) -> str:
        if name == "retrieve":
            q = str(args.get("query") or "")
            doc = str(args.get("doc") or "").strip()
            return (f"@{doc} · " if doc else "") + q[:120]
        if name == "fetch_section":
            return f"{args.get('doc', '')} › {args.get('section', '')}"[:120]
        if name == "web_search":
            return str(args.get("query") or "")[:120]
        return json.dumps(args, default=str)[:120]

    @staticmethod
    def _result_detail(name: str, result: dict) -> str:
        if name == "retrieve" and result.get("results") is not None:
            lines = []
            scope = result.get("scoped_to") or result.get("scope")
            if scope:
                lines.append(f"scope: {scope}")
            for r in (result.get("results") or [])[:4]:
                sec = r.get("section_path") or "(body)"
                lines.append(
                    f"- {r.get('filename', '?')} › {sec}"
                    f" (score={round(float(r.get('score') or 0), 3)})"
                )
            if result.get("note"):
                lines.append(str(result["note"]))
            return "\n".join(lines)[:1200]
        if name == "fetch_section" and result.get("text"):
            return str(result["text"])[:800]
        if result.get("summary"):
            return str(result["summary"])[:800]
        if result.get("error"):
            return str(result["error"])[:400]
        return ""

    @staticmethod
    def _summarize(result: dict) -> str:
        bits = []
        if result.get("results") is not None:
            n = len(result["results"])
            best = result.get("best_score")
            scope = result.get("scope") or ("document" if result.get("scoped_to") else "all")
            docs = sorted({r.get("filename") for r in result["results"] if r.get("filename")})
            bits.append(
                f"{n} chunks from {', '.join(docs[:3]) or 'KB'}"
                f" · best={best} · kb_hit={result.get('kb_hit')} · scope={scope}"
            )
        if result.get("text"):
            bits.append(str(result["text"])[:250])
        if result.get("summary"):
            bits.append(str(result["summary"])[:250])
        if result.get("error"):
            bits.append(f"error: {result['error']}"[:250])
        return " | ".join(bits) or json.dumps(result, default=str)[:300]

    def _fallback(self, text: str) -> AnswerOut:
        return AnswerOut(
            session_id=self.sid,
            answer=text or "I could not produce a grounded answer.",
            assumptions=["model answered without the required tool contract"],
            citations_valid=False,
        )