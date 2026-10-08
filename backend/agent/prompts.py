"""System prompt + tool declarations for the support-assistant agent loop."""

from google.genai import types


def system_prompt(kb_card: str, memory_card: str, pre: dict) -> str:
    rewritten = pre.get("rewritten_query") or ""
    intent = pre.get("intent") or "kb_lookup"
    repeat = int(pre.get("repeat_of_turn") or 0)
    switch = bool(pre.get("topic_switch"))
    focus = pre.get("focus_docs") or []
    if focus:
        names = ", ".join(f"@{d.get('mention') or d.get('filename')}" for d in focus)
        scope_line = (
            f"- DOCUMENT SCOPE: user @mentioned {names}. retrieve() is already"
            " constrained to ONLY those documents. Do not cite other docs."
        )
    else:
        scope_line = (
            "- DOCUMENT SCOPE: no @mention — search the whole knowledge base"
            " with semantic+keyword retrieve(). Users can type @DocAlias to"
            " focus one document."
        )
    repeat_line = ""
    if repeat:
        repeat_line = (
            f"- LIKELY REPEAT of turn {repeat}: the user re-asked something already"
            " answered. Do NOT repeat the full answer; briefly point back"
            f" (e.g. \"As covered in turn {repeat} ...\") and add only what is new."
        )
    switch_line = (
        "- TOPIC SWITCH detected: explicitly acknowledge it in one short sentence"
        " before answering (e.g. \"Switching topics to claims ...\"), and answer the"
        " new question on its own terms without dragging the old topic in."
        if switch
        else ""
    )
    return f"""You are DocScribe, a document-aware support assistant. You answer
ONLY from the knowledge base using the retrieve tool, and you cite the exact
document section every claim comes from. You are honest about what the KB
does not contain.

KNOWLEDGE BASE
{kb_card}

SESSION MEMORY
{memory_card}

THIS TURN (memory pre-pass already ran)
- user intent: {intent}
- rewritten query for retrieval: "{rewritten}"
{scope_line}
{repeat_line}
{switch_line}

TOOLS
- retrieve(query, k?, doc?): hybrid keyword+semantic search. Without doc it
  searches the active scope (whole KB, or @mentioned docs). Pass doc to
  further narrow to one filename. ALWAYS retrieve before answering from the
  KB. If kb_hit is false or scores are low, the KB likely does not cover it.
- fetch_section(doc, section): pull the exact, complete text of one document
  section. Use it when a citation must quote precisely or a chunk is cut off.
- web_search: ONLY when the KB does not cover the question. Anything from
  web_search must be clearly labelled as outside the KB in your answer, and
  must NOT appear in citations.
- submit_answer: the ONLY way to deliver a final answer.

CITATION RULES (strict)
1. Every factual claim in the answer must come from text you saw in a
   retrieve/fetch_section result this turn. Never answer from memory.
2. Every citation span_text must be a VERBATIM substring of a chunk you
   actually received - copy it exactly, do not paraphrase or reconstruct.
   Spans are string-matched against the KB before the answer is accepted;
   a failed match is sent back to you to fix.
2b. If your answer uses KB content, citations MUST be non-empty - an answer
   with no citations is rejected and sent back for repair. The only way to
   answer without citations is not_in_kb=true.
3. One citation per distinct claim is enough; do not cite the same span twice.
4. If the KB does not contain the answer: set not_in_kb=true, say so plainly,
   mention the closest sections the KB does have, and either offer a clearly
   labelled web answer or stop. Never fabricate a citation.
5. Set compressed=true when you pointed back to an earlier turn instead of
   repeating it. Set topic_switch_ack=true when you acknowledged a switch.
6. Write plainly: short paragraphs or tight bullets. No filler.

WORKFLOW
retrieve (inspect with fetch_section if needed) -> (web_search only if KB
misses) -> submit_answer with citations."""
KB_CARD_CAP = 3500


def kb_card(docs: list[dict], chunks_by_doc: dict[str, list[str]]) -> str:
    from backend.mentions import mention_alias

    lines = []
    for d in docs:
        sections = chunks_by_doc.get(d["doc_id"], [])
        uniq = list(dict.fromkeys(s for s in sections if s))[:12]
        alias = d.get("mention") or mention_alias(d["filename"])
        lines.append(
            f"- @{alias} · {d['filename']} ({d['fmt']}): sections: "
            + "; ".join(uniq or ["(single-section)"])
        )
    return "\n".join(lines)[:KB_CARD_CAP]


TOOL_DECLS = [
    types.FunctionDeclaration(
        name="retrieve",
        description=(
            "Hybrid (keyword + semantic) search over the knowledge base."
            " Optionally narrow to one document via doc=filename or @alias."
            " Returns chunk texts with doc/section/page metadata and scores."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING"},
                "k": {"type": "INTEGER", "description": "max chunks (default 6)"},
                "doc": {
                    "type": "STRING",
                    "description": "optional filename or @alias to scope search",
                },
            },
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="fetch_section",
        description=(
            "Fetch the exact full text of one document section for precise"
            " quoting/citation."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "doc": {"type": "STRING", "description": "document filename"},
                "section": {"type": "STRING", "description": "section title or path"},
            },
            "required": ["doc", "section"],
        },
    ),
    types.FunctionDeclaration(
        name="web_search",
        description=(
            "Web lookup, ONLY when the KB does not cover the question."
            " Results must be labelled as outside the KB and never cited."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING"}},
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="submit_answer",
        description="REQUIRED final response format. Citations are validated strictly.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "answer": {"type": "STRING"},
                "citations": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "doc": {"type": "STRING"},
                            "section": {"type": "STRING"},
                            "span_text": {"type": "STRING"},
                        },
                        "required": ["doc", "span_text"],
                    },
                },
                "not_in_kb": {"type": "BOOLEAN"},
                "compressed": {"type": "BOOLEAN"},
                "topic_switch_ack": {"type": "BOOLEAN"},
                "assumptions": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["answer"],
        },
    ),
]