"""Streamlit thin client: chat + KB manager + visible session memory (SSE)."""

import json
import os

import requests
import streamlit as st

BASE = os.environ.get("AGENT_URL", "http://localhost:8002")

st.set_page_config(page_title="Document Support Assistant", page_icon="📚", layout="wide")


def api(method: str, path: str, **kwargs):
    r = requests.request(method, f"{BASE}{path}", timeout=300, **kwargs)
    r.raise_for_status()
    return r


def ensure_session() -> str:
    if "sid" not in st.session_state:
        st.session_state.sid = api("POST", "/sessions").json()["session_id"]
    return st.session_state.sid


def parse_sse(response):
    event = "message"
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            yield event, json.loads(line[len("data: ") :])


TOOL_ICONS = {"retrieve": "🔎", "fetch_section": "📄", "web_search": "🌐"}


def render_citations(citations: list[dict], chunks_lookup: dict | None = None):
    for c in citations or []:
        badge = "✅" if c.get("valid", True) else "⚠️ unverified"
        label = c.get("doc", "?")
        if c.get("section"):
            label += f" › {c['section']}"
        with st.expander(f"{badge} 📎 {label}"):
            st.markdown(f"> {c.get('span_text', '')}")


def render_assistant(msg: dict):
    with st.chat_message("assistant"):
        if msg.get("not_in_kb"):
            st.warning("🚫 Not covered by the knowledge base")
        if msg.get("compressed"):
            st.info("↩️ Pointed back to an earlier answer instead of repeating it")
        if msg.get("topic_switch_ack"):
            st.caption("🔀 Topic switch acknowledged")
        if msg.get("citations") and not msg.get("citations_valid", True):
            st.error("Some citations could not be verified against the KB")
        st.markdown(msg["content"])
        render_citations(msg.get("citations", []))
        if msg.get("web_sources"):
            with st.expander("🌐 Web sources (outside the KB)"):
                for s in msg["web_sources"]:
                    st.markdown(f"- [{s.get('title') or s.get('uri')}]({s.get('uri')})")
        if msg.get("memory", {}).get("rewritten_query"):
            with st.expander("🧠 Memory trace for this turn"):
                mem = msg["memory"]
                st.markdown(
                    f"Intent: **{mem.get('intent')}** · rewritten query:"
                    f" *{mem.get('rewritten_query')}* · repeat:"
                    f" **{mem.get('likely_repeat')}** · switch:"
                    f" **{mem.get('topic_switch')}**"
                )


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("📚 Document Support Assistant")
    st.caption("LangChain RAG · Gemini · verified citations · session memory")

    st.markdown("### Knowledge base")
    uploaded = st.file_uploader("Add document (PDF / DOCX / MD)", type=["pdf", "docx", "md"])
    if uploaded and st.button("Ingest document"):
        with st.spinner("Parsing → chunking → embedding → indexing..."):
            try:
                resp = api(
                    "POST", "/documents",
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
                )
                info = resp.json()
                st.success(f"Ingested: {info['filename']} ({info['n_chunks']} chunks)")
                st.session_state.pop("docs", None)
                st.rerun()
            except requests.HTTPError as exc:
                detail = "upload failed"
                try:
                    detail = exc.response.json().get("detail", detail)
                except Exception:  # noqa: BLE001
                    pass
                st.error(f"❌ {detail}")

    if "docs" not in st.session_state:
        try:
            st.session_state.docs = api("GET", "/documents").json()
        except Exception:  # noqa: BLE001
            st.session_state.docs = []
    for d in st.session_state.get("docs", []):
        c1, c2 = st.columns([4, 1])
        c1.markdown(
            f"**{d['filename']}** \n"
            f"<small>{d['fmt']} · {d['n_chunks']} chunks · {d['n_sections']} sections</small>",
            unsafe_allow_html=True,
        )
        if c2.button("🗑", key=f"del_{d['doc_id']}"):
            api("DELETE", f"/documents/{d['doc_id']}")
            st.session_state.pop("docs", None)
            st.rerun()

    st.divider()
    st.markdown("### 🧠 Session memory")
    sid = ensure_session()
    try:
        mem = api("GET", f"/sessions/{sid}/memory").json()
        topics = mem.get("topics", [])
        asked = mem.get("asked_questions", [])
        if topics:
            st.markdown(
                "**Topics:** "
                + ", ".join(f"`{t['topic']}`" for t in topics)
            )
        if asked:
            st.markdown("**Asked:**")
            for a in asked[-6:]:
                st.markdown(f"- T{a['turn']}: {a['question'][:70]}")
        if mem.get("summary"):
            with st.expander("Rolling summary (compacted)"):
                st.caption(mem["summary"])
        if not topics and not asked:
            st.caption("Nothing discussed yet.")
    except Exception:  # noqa: BLE001
        st.caption("Memory unavailable.")

    usage = st.session_state.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0})
    st.divider()
    st.markdown("### Session cost")
    c1, c2 = st.columns(2)
    c1.metric("Prompt tok", f"{usage['prompt_tokens']:,}")
    c2.metric("Completion tok", f"{usage['completion_tokens']:,}")
    st.metric("LLM calls", usage["llm_calls"])

# ---------------------------------------------------------------- chat state
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        render_assistant(msg)

sid = ensure_session()

if question := st.chat_input("Ask about the documents..."):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        trace_box = st.container()
        answer_holder = st.empty()

        with requests.post(
            f"{BASE}/sessions/{sid}/messages",
            params={"stream": "true"},
            json={"question": question},
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                st.error(resp.json().get("detail", "backend error"))
                st.stop()
            answer = None
            for event, data in parse_sse(resp):
                if event == "memory":
                    with trace_box:
                        rq = data.get("rewritten_query", "")
                        st.caption(
                            f"🧠 intent **{data.get('intent')}** · query: *{rq}*"
                            + (
                                f" · likely repeat of turn {data.get('repeat_of_turn')}"
                                if data.get("likely_repeat")
                                else ""
                            )
                            + (" · 🔀 topic switch" if data.get("topic_switch") else "")
                        )
                elif event == "tool_call":
                    with trace_box:
                        icon = TOOL_ICONS.get(data["name"], "🔧")
                        st.markdown(f"{icon} **{data['name']}**")
                        if data["args"].get("query"):
                            st.caption(f"query: {data['args']['query']}")
                        elif data["args"].get("doc"):
                            st.caption(f"{data['args'].get('doc')} › {data['args'].get('section', '')}")
                elif event == "tool_result":
                    with trace_box:
                        status = "✅" if data["ok"] else "❌"
                        st.caption(f"{status} {data['summary'][:220]} ({data['latency_ms']} ms)")
                elif event == "answer":
                    answer = data
                elif event == "error":
                    st.error(f"agent error: {data.get('message')}")
            if answer:
                usage = st.session_state.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0})
                for key in ("prompt_tokens", "completion_tokens", "llm_calls"):
                    usage[key] = usage.get(key, 0) + answer.get("usage", {}).get(key, 0)
                st.session_state.usage = usage
                st.session_state.pop("docs", None)  # KB card may be stale

                msg = {
                    "role": "assistant",
                    "content": answer["answer"],
                    "citations": answer.get("citations", []),
                    "citations_valid": answer.get("citations_valid", True),
                    "not_in_kb": answer.get("not_in_kb", False),
                    "compressed": answer.get("compressed", False),
                    "topic_switch_ack": answer.get("topic_switch_ack", False),
                    "web_sources": answer.get("web_sources", []),
                    "memory": answer.get("memory", {}),
                }
                st.session_state.messages.append(msg)
                render_assistant(msg)