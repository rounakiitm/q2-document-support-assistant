# Document Support Assistant (Q2)

**Prod demo URL:** https://q2-support-assistant-yp4xrztlaq-el.a.run.app/app/

A multi-turn **document support assistant** over your own PDF / DOCX / Markdown
files. It retrieves with hybrid search, remembers what you already asked,
handles topic switches, and **only cites spans that string-match the indexed
source** before the answer is shown.

## Prod demo URLs

| | URL |
|---|---|
| **Prod demo (UI)** | https://q2-support-assistant-yp4xrztlaq-el.a.run.app/app/ |
| **API base** | https://q2-support-assistant-yp4xrztlaq-el.a.run.app |
| **Health** | https://q2-support-assistant-yp4xrztlaq-el.a.run.app/health |
| **OpenAPI docs** | https://q2-support-assistant-yp4xrztlaq-el.a.run.app/docs |
| **Source** | https://github.com/rounakiitm/q2-document-support-assistant |

Hosted on **Google Cloud Run** (`rounak-iitm`, `asia-south1`). UI and API are
the same service (`/app` for the SPA).

## Authentication

**No login / no Supabase on Q2.** The demo is open:

- Anyone with the URL can use the UI and API (Cloud Run `--allow-unauthenticated`).
- Chat sessions are identified by a client-generated / server-issued **session id**
  stored in the browser — not by a user account.
- Knowledge base, messages, and memory live in **local SQLite + Chroma** on the
  Cloud Run instance (ephemeral across cold starts / new revisions; fine for demo).
- There is no JWT, password form, or manual admin gate.

Open the UI, use the seeded insurance KB (or upload your own docs), chat in the
main panel. Use `@document-alias` to focus retrieval on one file. Expand the
agent context panel to see memory events, tool calls, and verified citations.

## What it does

1. **Ingest** — upload PDF/DOCX/MD → parse → chunk → embed → live Chroma index + SQLite metadata.
2. **Memory pre-pass** — rewrite query, detect repeats, acknowledge topic switches, update topic ledger.
3. **Agent loop** (up to 20 reasoning steps) with tools:
   - `retrieve` — hybrid BM25 + dense (RRF)
   - `fetch_section` — exact section text for citation
   - `web_search` — labelled non-KB fallback
   - `submit_answer` — Pydantic answer with `citations[]`
4. **Citation gate** — every quoted span must appear in a stored chunk; else repair (max 2) or flag.
5. **SSE stream** — UI shows memory / tools / final answer.

```
Vanilla JS SPA (/app)
    │  SSE
    ▼
FastAPI (Cloud Run)
    ├── Memory (topic ledger · asked-log · rolling summary)
    ├── Gemini agent (function calling)
    │     ├── retrieve  → BM25 + Chroma dense, RRF fusion
    │     ├── fetch_section
    │     ├── web_search (labelled)
    │     └── submit_answer + citation validator
    └── SQLite (docs/chunks/sessions) + Chroma (vectors)
```

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Vanilla HTML / CSS / JS SPA | Dedicated chat UI with @mentions, sessions, collapsible agent context |
| API | **FastAPI** + **Uvicorn** | SSE multi-turn chat |
| LLM | **Google Gemini** (`google-genai`) | Agent loop + memory rewrite |
| RAG loaders | **LangChain** (PyPDF, python-docx, MarkdownHeaderTextSplitter) | Reliable multi-format ingest |
| Chunking | LangChain text splitters | Header-aware + recursive splits |
| Embeddings | **GoogleGenerativeAIEmbeddings** | Same Gemini key pool |
| Vector store | **Chroma** | Local/ephemeral index on Cloud Run |
| Lexical search | **rank-bm25** | Keyword path for hybrid retrieval |
| Fusion | Reciprocal Rank Fusion (RRF) | Combine sparse + dense rankings |
| Session store | **SQLite** | Messages, topics, asked questions, chunk text for BM25/citations |
| Eval | `evals/conversation.yaml` + harness | 12-turn scripted scoring |
| Hosting | **Google Cloud Run** | Docker image; Gemini keys via Secret Manager |
| Agent cap | `AGENT_MAX_ITERATIONS=20` | Enough steps for multi-tool turns |

## How requirements map to mechanisms

| Requirement | Mechanism |
|---|---|
| Own documents as KB | Runtime upload path (PDF/DOCX/MD); sample pack auto-seeded |
| Focus one document | `@alias` scopes hybrid retrieval to that doc |
| Real-time intake | `POST /documents` → parse → chunk → embed → live index |
| Multi-turn context | Recent turns verbatim; older turns → rolling summary |
| Remembers prior questions | Per-session asked-question log (sidebar) |
| Avoids repeating | Embedding similarity + LLM pre-pass → compressed re-answers |
| Topic switches | Explicit acknowledgement + coreference rewrite |
| Section citations | `{doc, section, span_text}` with substring validation |
| ≥10-turn demo | 12-turn eval harness → `eval_report.md` |

## Run locally

```bash
pip install -r requirements.txt
python scripts/make_sample_docs.py   # sample KB under data/seed_docs
cp .env.example .env                 # GEMINI_API_KEYS / MODEL_NAME
uvicorn backend.main:app --port 8002
# UI: http://localhost:8002/app
```

Docker:

```bash
cp .env.example .env
docker compose up --build
```

Cloud Run redeploy: `.\scripts\deploy_cloudrun.ps1` (or the WSL `.sh` variant).

## Tests & eval

```bash
pytest -q
python evals/run_eval.py       # needs a running backend
```

## Main API

```
GET    /health
GET    /documents
POST   /documents
DELETE /documents/{doc_id}
GET    /sessions
POST   /sessions
GET    /sessions/{id}/messages
GET    /sessions/{id}/memory
POST   /sessions/{id}/messages?stream=true
DELETE /sessions/{id}
```

## Design decisions

- **LangChain for plumbing, custom code for graded behaviour** — citation validation,
  topic ledger, and honest “not in KB” paths are hand-built and tested.
- **Citations are validated, not trusted** — a span must exist in the index.
- **SQLite + Chroma** — chunk text in SQLite for BM25 + validators; Chroma for dense search.
