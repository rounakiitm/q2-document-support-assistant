"""Knowledge base: file -> chunks -> SQLite rows + Chroma vector index (live).

One Chroma collection for the whole KB; per-document deletion uses a `where`
filter on `doc_id`. Chunk texts also live in SQLite so BM25 and the citation
validator can work without touching the vector store.
"""

import hashlib
import logging
import uuid
from pathlib import Path

from backend.config import settings
from backend.storage.db import DB

log = logging.getLogger("ingest")


def _default_embeddings():
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model, google_api_key=settings.effective_api_key
    )


def _embeddings_with_key(api_key: str):
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    return GoogleGenerativeAIEmbeddings(model=settings.embedding_model, google_api_key=api_key)


def _default_vectorstore(embeddings):
    from langchain_chroma import Chroma

    return Chroma(
        collection_name="kb_chunks",
        embedding_function=embeddings,
        persist_directory=str(settings.chroma_dir),
        collection_metadata={"hnsw:space": "cosine"},
    )


class KnowledgeBase:
    def __init__(self, db: DB, embeddings=None, vectorstore=None, threshold=None):
        self.db = db
        self._key_pool = settings.api_keys or [settings.effective_api_key]
        self._key_idx = 0
        self.embeddings = embeddings or _default_embeddings()
        self.vectorstore = vectorstore or _default_vectorstore(self.embeddings)
        self.threshold = (
            threshold if threshold is not None else settings.kb_score_threshold
        )
        self._bm25 = None
        self._bm25_chunks: list[dict] = []
        self.rebuild_bm25()

    # ------------------------------------------------- embedding resilience
    def _rotate_embeddings(self) -> bool:
        """Rebuild the embedder on the next key in the pool (own quota bucket)."""
        self._key_idx += 1
        if self._key_idx >= len(self._key_pool):
            return False
        log.warning("embedding 429: rotating to key #%d", self._key_idx + 1)
        self.embeddings = _embeddings_with_key(self._key_pool[self._key_idx])
        self.vectorstore = _default_vectorstore(self.embeddings)
        return True

    def _run_with_embedding_rotation(self, fn):  # noqa: ANN001, ANN201
        """Run an embedding-backed operation, rotating keys on 429.

        Embedding quota is per-minute per-project; a different key = a
        different bucket, so rotation avoids waiting out the minute window.
        """
        last: Exception | None = None
        for _ in range(len(self._key_pool)):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                last = exc
                if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                    if not self._rotate_embeddings():
                        break
                    continue
                raise
        raise last  # type: ignore[misc]

    # ------------------------------------------------------------- ingest
    def ingest_file(self, path: Path, filename: str) -> dict:
        from backend.ingest.loader import fmt_for, load_any

        fmt = fmt_for(filename)
        if not fmt:
            raise ValueError("only .pdf / .docx / .md files are supported")

        # re-ingesting the same filename replaces the previous version
        for d in self.list_documents():
            if d["filename"] == filename:
                self.delete(d["doc_id"])

        doc_id = hashlib.md5(filename.encode()).hexdigest()[:10] + uuid.uuid4().hex[:4]
        docs = load_any(path, fmt)
        if not docs:
            raise ValueError("no text could be extracted from this file")

        chunks = []
        metadatas = []
        for i, d in enumerate(docs):
            meta = {
                "doc_id": doc_id,
                "filename": filename,
                "section_path": d.metadata.get("section_path", ""),
                "page": d.metadata.get("page"),
            }
            chunks.append(meta | {"text": d.page_content})
            metadatas.append(meta)

        self.db.add_document(
            doc_id, filename, fmt, Path(filename).stem.replace("_", " ").title()
        )
        self.db.add_chunks(doc_id, chunks)
        self._run_with_embedding_rotation(
            lambda: self.vectorstore.add_texts(
                [d.page_content for d in docs],
                metadatas=metadatas,
                ids=[f"{doc_id}:{i}" for i in range(len(docs))],
            )
        )
        self.rebuild_bm25()
        log.info("ingested %s (%d chunks, %s)", filename, len(chunks), fmt)
        return {
            "doc_id": doc_id,
            "filename": filename,
            "fmt": fmt,
            "n_chunks": len(chunks),
        }

    def delete(self, doc_id: str) -> None:
        try:
            self.vectorstore.delete(where={"doc_id": doc_id})
        except Exception:  # noqa: BLE001 - DB rows are the source of truth
            log.exception("chroma delete failed for %s; dropping db rows anyway", doc_id)
        self.db.delete_document(doc_id)
        self.rebuild_bm25()

    def list_documents(self) -> list[dict]:
        return self.db.get_documents()

    def dense_search(
        self, query: str, k: int, doc_ids: list[str] | None = None
    ) -> list[tuple[dict, float]]:
        """Dense retrieval with the same 429 key-rotation protection."""
        where = None
        if doc_ids:
            if len(doc_ids) == 1:
                where = {"doc_id": doc_ids[0]}
            else:
                where = {"doc_id": {"$in": list(doc_ids)}}

        def _search():
            if where is None:
                return self.vectorstore.similarity_search_with_score(query, k=k)
            return self.vectorstore.similarity_search_with_score(
                query, k=k, filter=where
            )

        return self._run_with_embedding_rotation(_search)

    # ------------------------------------------------------------- bm25
    def rebuild_bm25(self) -> None:
        from rank_bm25 import BM25Okapi

        self._bm25_chunks = self.db.get_chunks()
        if not self._bm25_chunks:
            self._bm25 = None
            return
        tokenized = [_tokenize(c["text"]) for c in self._bm25_chunks]
        self._bm25 = BM25Okapi(tokenized)

    def bm25_search(
        self, query: str, k: int, doc_ids: list[str] | None = None
    ) -> list[tuple[dict, float]]:
        if self._bm25 is None:
            return []
        allow = set(doc_ids) if doc_ids else None
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda p: p[1], reverse=True)
        out: list[tuple[dict, float]] = []
        for i, s in ranked:
            if s <= 0:
                continue
            chunk = self._bm25_chunks[i]
            if allow is not None and chunk.get("doc_id") not in allow:
                continue
            out.append((chunk, float(s)))
            if len(out) >= k:
                break
        return out


def _tokenize(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", text.lower())