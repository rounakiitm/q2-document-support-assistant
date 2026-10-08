import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    """Thread-safe SQLite persistence for the KB, sessions, memory and traces."""

    def __init__(self, path: Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents(
                    doc_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    fmt TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    n_chunks INTEGER NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks(
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    section_path TEXT NOT NULL DEFAULT '',
                    page INTEGER,
                    text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, role TEXT NOT NULL,
                    content TEXT NOT NULL, meta TEXT, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tool_runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, tool TEXT NOT NULL,
                    args TEXT, result TEXT, ok INTEGER, latency_ms REAL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    prompt_tokens INTEGER, completion_tokens INTEGER,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS topics(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    first_turn INTEGER NOT NULL,
                    last_turn INTEGER NOT NULL,
                    UNIQUE(session_id, topic));
                CREATE TABLE IF NOT EXISTS asked_questions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT 'kb_lookup',
                    topics TEXT NOT NULL DEFAULT '[]',
                    answer_summary TEXT NOT NULL DEFAULT '',
                    question_embedding TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL);
                """
            )

    # sessions -------------------------------------------------------------
    def create_session(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO sessions(id, created_at) VALUES(?, ?)", (sid, utcnow())
            )
        return sid

    def list_sessions(self, limit: int = 40) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                """
                SELECT s.id, s.created_at, s.summary,
                       (SELECT content FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'user'
                        ORDER BY m.id ASC LIMIT 1) AS preview,
                       (SELECT COUNT(*) FROM messages m
                        WHERE m.session_id = s.id) AS n_messages,
                       COALESCE(
                         (SELECT MAX(m.created_at) FROM messages m
                          WHERE m.session_id = s.id),
                         s.created_at
                       ) AS last_active
                FROM sessions s
                ORDER BY last_active DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def session_exists(self, sid: str) -> bool:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
        return row is not None

    def delete_session(self, sid: str) -> None:
        with self._lock, self._conn() as c:
            for table in ("messages", "tool_runs", "usage", "topics", "asked_questions"):
                c.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))

    def get_summary(self, sid: str) -> str:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
        return row["summary"] if row else ""

    def set_summary(self, sid: str, summary: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE sessions SET summary=? WHERE id=?", (summary, sid))

    # messages ---------------------------------------------------------------
    def add_message(
        self, sid: str, role: str, content: str, meta: dict | None = None
    ) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO messages(session_id, role, content, meta, created_at)"
                " VALUES(?,?,?,?,?)",
                (sid, role, content, json.dumps(meta or {}), utcnow()),
            )

    def count_messages(self, sid: str) -> int:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (sid,)
            ).fetchone()
        return row["n"]

    def get_messages(self, sid: str) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT role, content, meta, created_at FROM messages"
                " WHERE session_id=? ORDER BY id",
                (sid,),
            ).fetchall()
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "meta": json.loads(r["meta"] or "{}"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # tool runs --------------------------------------------------------------
    def add_tool_run(
        self,
        sid: str,
        tool: str,
        args: dict | str,
        result: dict | str,
        ok: bool,
        latency_ms: float,
    ) -> None:
        args_s = args if isinstance(args, str) else json.dumps(args, default=str)
        res_s = result if isinstance(result, str) else json.dumps(result, default=str)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO tool_runs(session_id, tool, args, result, ok,"
                " latency_ms, created_at) VALUES(?,?,?,?,?,?,?)",
                (sid, tool, args_s[:4000], res_s[:8000], int(ok), latency_ms, utcnow()),
            )

    def get_tool_runs(self, sid: str, limit: int | None = None) -> list[dict]:
        q = (
            "SELECT tool, args, result, ok, latency_ms, created_at FROM tool_runs"
            " WHERE session_id=? ORDER BY id"
        )
        if limit:
            q += f" DESC LIMIT {int(limit)}"
        with self._lock, self._conn() as c:
            rows = c.execute(q, (sid,)).fetchall()
        runs = [
            {
                "tool": r["tool"],
                "args": r["args"],
                "result": r["result"],
                "ok": bool(r["ok"]),
                "latency_ms": r["latency_ms"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return list(reversed(runs)) if limit else runs

    # usage --------------------------------------------------------------------
    def add_usage(self, sid: str, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO usage(session_id, prompt_tokens, completion_tokens,"
                " created_at) VALUES(?,?,?,?)",
                (sid, prompt_tokens, completion_tokens, utcnow()),
            )

    def get_usage(self, sid: str) -> dict:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(prompt_tokens),0) AS p,"
                " COALESCE(SUM(completion_tokens),0) AS c,"
                " COUNT(*) AS calls FROM usage WHERE session_id=?",
                (sid,),
            ).fetchone()
        return {
            "prompt_tokens": row["p"],
            "completion_tokens": row["c"],
            "llm_calls": row["calls"],
        }

    # documents / chunks (the KB) ------------------------------------------------
    def add_document(self, doc_id: str, filename: str, fmt: str, title: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO documents(doc_id, filename, fmt, title, uploaded_at)"
                " VALUES(?,?,?,?,?)",
                (doc_id, filename, fmt, title, utcnow()),
            )

    def get_documents(self) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT d.doc_id, d.filename, d.fmt, d.title, d.uploaded_at,"
                " COUNT(k.chunk_id) AS n_chunks,"
                " COUNT(DISTINCT k.section_path) AS n_sections"
                " FROM documents d LEFT JOIN chunks k ON k.doc_id = d.doc_id"
                " GROUP BY d.doc_id ORDER BY d.uploaded_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: str) -> dict | None:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT d.doc_id, d.filename, d.fmt, d.title, d.uploaded_at,"
                " COUNT(k.chunk_id) AS n_chunks,"
                " COUNT(DISTINCT k.section_path) AS n_sections"
                " FROM documents d LEFT JOIN chunks k ON k.doc_id = d.doc_id"
                " WHERE d.doc_id=? GROUP BY d.doc_id",
                (doc_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_document(self, doc_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            c.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))

    def add_chunks(self, doc_id: str, chunks: list[dict]) -> None:
        with self._lock, self._conn() as c:
            c.executemany(
                "INSERT INTO chunks(doc_id, section_path, page, text) VALUES(?,?,?,?)",
                [
                    (
                        doc_id,
                        ch.get("section_path", ""),
                        ch.get("page"),
                        ch["text"],
                    )
                    for ch in chunks
                ],
            )
            c.execute(
                "UPDATE documents SET n_chunks = n_chunks + ? WHERE doc_id=?",
                (len(chunks), doc_id),
            )

    def get_chunks(self, doc_id: str | None = None) -> list[dict]:
        q = (
            "SELECT chunk_id, doc_id, section_path, page, text FROM chunks"
        )
        params: tuple = ()
        if doc_id:
            q += " WHERE doc_id=?"
            params = (doc_id,)
        with self._lock, self._conn() as c:
            rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    # memory: topics + asked questions --------------------------------------------
    def upsert_topic(self, sid: str, topic: str, turn: int) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO topics(session_id, topic, first_turn, last_turn)"
                " VALUES(?,?,?,?)"
                " ON CONFLICT(session_id, topic)"
                " DO UPDATE SET last_turn=excluded.last_turn",
                (sid, topic.lower(), turn, turn),
            )

    def get_topics(self, sid: str) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT topic, first_turn, last_turn FROM topics"
                " WHERE session_id=? ORDER BY first_turn",
                (sid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_asked_question(
        self,
        sid: str,
        turn: int,
        question: str,
        intent: str,
        topics: list[str],
        answer_summary: str,
        question_embedding: str,
    ) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO asked_questions(session_id, turn, question, intent,"
                " topics, answer_summary, question_embedding, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    sid,
                    turn,
                    question,
                    intent,
                    json.dumps(topics),
                    answer_summary[:600],
                    question_embedding,
                    utcnow(),
                ),
            )

    def get_asked_questions(self, sid: str) -> list[dict]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT turn, question, intent, topics, answer_summary,"
                " question_embedding FROM asked_questions"
                " WHERE session_id=? ORDER BY turn",
                (sid,),
            ).fetchall()
        return [
            {
                "turn": r["turn"],
                "question": r["question"],
                "intent": r["intent"],
                "topics": json.loads(r["topics"] or "[]"),
                "answer_summary": r["answer_summary"],
                "question_embedding": r["question_embedding"],
            }
            for r in rows
        ]