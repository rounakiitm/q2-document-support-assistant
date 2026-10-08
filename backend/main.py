"""FastAPI backend: KB management, sessions, SSE chat with memory events."""

import hashlib
import json
import logging
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from google.genai import types

from backend.agent.core import Agent
from backend.config import settings
from backend.llm.client import LLMClient
from backend.logging_conf import setup_logging
from backend.memory.memory import SessionMemory
from backend.mentions import enrich_documents, resolve_mentions
from backend.retrieval.retriever import HybridRetriever
from backend.schemas import ChatMessageIn, DocumentInfo, SessionInfo
from backend.storage.db import DB
from backend.tools.search_tool import SearchTool

setup_logging(settings.log_level)
log = logging.getLogger("api")

app = FastAPI(title="Document Support Assistant", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024

db = DB(settings.db_path)
llm = LLMClient()
search = SearchTool(llm)

_kb = None
_retriever = None
_lock = threading.Lock()


def get_kb():
    with _lock:
        if _kb is None:
            raise HTTPException(503, "knowledge base not ready")
        return _kb


@app.on_event("startup")
def init_kb() -> None:
    global _kb, _retriever
    try:
        from backend.ingest.store import KnowledgeBase

        _kb = KnowledgeBase(db)
        _retriever = HybridRetriever(_kb)
    except Exception:
        log.exception("knowledge base init failed (missing GEMINI_API_KEY?)")
        return
    seed_flag = settings.uploads_dir / "seeded.flag"
    if not seed_flag.exists():
        failures = _seed_kb()
        if not failures:
            seed_flag.write_text("ok")
    log.info("KB ready: %d documents", len(_kb.list_documents()))


def _seed_kb() -> int:
    """Load sample docs through the same upload path (no pre-baked index)."""
    seed_dir = settings.seed_docs_dir
    if not seed_dir.exists():
        return 0
    failures = 0
    for f in sorted(seed_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            try:
                _kb.ingest_file(f, f.name)
            except Exception:
                failures += 1
                log.exception("seeding failed for %s", f.name)
    return failures


def build_agent(session_id: str, on_event=None) -> Agent:
    return Agent(llm, get_kb(), _retriever, search, db, session_id, on_event)


def load_history(session_id: str) -> list:
    msgs = db.get_messages(session_id)[-8:]
    return [
        types.Content(role=m["role"], parts=[types.Part(text=m["content"])])
        for m in msgs
        if m["role"] in ("user", "assistant")
    ]


# ------------------------------------------------------------------------ KB
@app.get("/")
def root():
    return RedirectResponse(url="/app/")


@app.get("/health")
def health():
    docs = _kb.list_documents() if _kb else []
    return {
        "status": "ok" if _kb else "degraded",
        "model": settings.model_name,
        "embedding_model": settings.embedding_model,
        "documents": len(docs),
        "chunks": sum(d["n_chunks"] for d in docs),
        "max_upload_mb": settings.max_upload_mb,
    }


def _doc_info(d: dict) -> DocumentInfo:
    enriched = enrich_documents([d])[0]
    return DocumentInfo(**{k: enriched[k] for k in DocumentInfo.model_fields if k in enriched})


@app.get("/documents", response_model=list[DocumentInfo])
def list_documents():
    return [_doc_info(d) for d in get_kb().list_documents()]


@app.post("/documents", response_model=DocumentInfo)
async def upload_document(file: UploadFile = File(...)):  # noqa: B008
    filename = Path(file.filename or "upload.md").name
    ext = Path(filename).suffix.lower()
    if ext not in settings.upload_exts:
        raise HTTPException(
            400,
            f"unsupported file type '{ext or '(none)'}'. "
            f"Allowed: {', '.join(sorted(settings.upload_exts))}",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            400, f"file exceeds {settings.max_upload_mb} MB upload limit"
        )
    dest = (
        settings.uploads_dir
        / f"{hashlib.md5(filename.encode()).hexdigest()[:8]}_{filename}"
    )
    dest.write_bytes(raw)
    try:
        info = get_kb().ingest_file(dest, filename)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"could not ingest document: {exc}") from exc
    return _doc_info(get_kb().db.get_document(info["doc_id"]))


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    get_kb().delete(doc_id)
    return {"ok": True}


# ------------------------------------------------------------------ sessions
@app.get("/sessions")
def list_sessions():
    return db.list_sessions()


@app.post("/sessions", response_model=SessionInfo)
def create_session():
    return SessionInfo(session_id=db.create_session())


@app.get("/sessions/{session_id}/messages")
def get_messages(session_id: str):
    if not db.session_exists(session_id):
        raise HTTPException(404, "session not found")
    return db.get_messages(session_id)


@app.get("/sessions/{session_id}/memory")
def get_memory(session_id: str):
    if not db.session_exists(session_id):
        raise HTTPException(404, "session not found")
    return {
        "topics": db.get_topics(session_id),
        "asked_questions": [
            {k: v for k, v in a.items() if k != "question_embedding"}
            for a in db.get_asked_questions(session_id)
        ],
        "summary": db.get_summary(session_id),
        "usage": db.get_usage(session_id),
    }


@app.get("/sessions/{session_id}/usage")
def get_usage(session_id: str):
    if not db.session_exists(session_id):
        raise HTTPException(404, "session not found")
    return db.get_usage(session_id)


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if not db.session_exists(session_id):
        raise HTTPException(404, "session not found")
    db.delete_session(session_id)
    return {"ok": True}


# ----------------------------------------------------------------------- chat
def _prepare_turn(session_id: str, question: str) -> dict:
    """Memory pre-pass + @mention resolution for document-scoped retrieval."""
    mention = resolve_mentions(question, get_kb().list_documents())
    memory = SessionMemory(db, llm, session_id, embedder=_embedder())
    # Use cleaned question (mentions stripped) for rewrite / repeat detection
    pre = memory.pre_turn(mention["clean_question"] or question)
    pre["focus_docs"] = mention["focus_docs"]
    pre["focus_doc_ids"] = mention["focus_doc_ids"]
    pre["mention_unresolved"] = mention["unresolved"]
    pre["scope"] = mention["scope"]
    pre["original_question"] = question
    return {"memory": memory, "pre": pre, "mention": mention}


@app.post("/sessions/{session_id}/messages")
def post_message(session_id: str, body: ChatMessageIn, stream: bool = True):
    if not db.session_exists(session_id):
        raise HTTPException(404, "session not found")
    get_kb()

    db.add_message(session_id, "user", body.question)
    history = load_history(session_id)[:-1]  # exclude the just-added turn

    if not stream:
        packed = _prepare_turn(session_id, body.question)
        agent = build_agent(session_id)
        answer = agent.ask(body.question, packed["pre"], history=history)
        packed["memory"].post_turn(body.question, answer.answer, packed["pre"])
        return answer.model_dump()

    event_queue: queue.Queue = queue.Queue()

    def run_agent():
        try:
            packed = _prepare_turn(session_id, body.question)
            pre = packed["pre"]
            event_queue.put(
                (
                    "memory",
                    {
                        k: v
                        for k, v in pre.items()
                        if k != "question_embedding"
                    },
                )
            )
            agent = build_agent(
                session_id, on_event=lambda ev, d: event_queue.put((ev, d))
            )
            answer = agent.ask(body.question, pre, history=history)
            packed["memory"].post_turn(body.question, answer.answer, pre)
            event_queue.put(("answer", answer.model_dump()))
        except Exception as exc:
            log.exception("agent failed")
            event_queue.put(("error", {"message": str(exc)}))
        event_queue.put(None)

    def event_stream():
        threading.Thread(target=run_agent, daemon=True).start()
        while True:
            item = event_queue.get()
            if item is None:
                break
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# -------------------------------------------------------------------- frontend
from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount(
    "/app",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True),
    name="frontend",
)


def _embedder():
    try:
        return get_kb().embeddings.embed_query
    except Exception:  # noqa: BLE001 - repeat detection degrades gracefully
        return None