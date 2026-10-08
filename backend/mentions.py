"""Parse @document mentions from a user question and resolve them to KB docs."""

from __future__ import annotations

import re
from pathlib import Path

MENTION_RE = re.compile(r"(?:^|[\s(\[])@([A-Za-z0-9][A-Za-z0-9_.-]*)")


def mention_alias(filename: str) -> str:
    """Stable @alias derived from a document filename (no extension)."""
    stem = Path(filename or "doc").stem
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return cleaned or "doc"


def enrich_documents(docs: list[dict]) -> list[dict]:
    out = []
    for d in docs:
        item = dict(d)
        item["mention"] = mention_alias(item.get("filename") or item.get("doc_id") or "doc")
        out.append(item)
    return out


def parse_mentions(question: str) -> list[str]:
    return [m.group(1) for m in MENTION_RE.finditer(question or "")]


def resolve_mentions(question: str, documents: list[dict]) -> dict:
    """Return focus docs for @mentions, or empty focus for whole-KB search.

    Matching is case-insensitive against mention alias, filename stem, and full
    filename. Unresolved tokens are returned so the UI can warn.
    """
    tokens = parse_mentions(question)
    docs = enrich_documents(documents)
    if not tokens:
        return {
            "focus_docs": [],
            "focus_doc_ids": [],
            "unresolved": [],
            "scope": "all",
            "clean_question": (question or "").strip(),
        }

    by_alias: dict[str, dict] = {}
    for d in docs:
        keys = {
            d["mention"].casefold(),
            Path(d.get("filename") or "").stem.casefold(),
            (d.get("filename") or "").casefold(),
            (d.get("doc_id") or "").casefold(),
        }
        for key in keys:
            if key:
                by_alias.setdefault(key, d)

    focus: list[dict] = []
    seen: set[str] = set()
    unresolved: list[str] = []
    for tok in tokens:
        hit = by_alias.get(tok.casefold())
        if not hit:
            # prefix match as a convenience (@policy → policy_wording)
            prefix_hits = [
                d for d in docs
                if d["mention"].casefold().startswith(tok.casefold())
                or Path(d.get("filename") or "").stem.casefold().startswith(tok.casefold())
            ]
            hit = prefix_hits[0] if len(prefix_hits) == 1 else None
        if not hit:
            unresolved.append(tok)
            continue
        if hit["doc_id"] in seen:
            continue
        seen.add(hit["doc_id"])
        focus.append(
            {
                "doc_id": hit["doc_id"],
                "filename": hit["filename"],
                "mention": hit["mention"],
            }
        )

    # Strip resolved @tokens from the question used for retrieval rewrite context
    clean = question or ""
    for tok in tokens:
        clean = re.sub(rf"(^|[\s(\[])@{re.escape(tok)}\b", r"\1", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    return {
        "focus_docs": focus,
        "focus_doc_ids": [d["doc_id"] for d in focus],
        "unresolved": unresolved,
        "scope": "document" if focus else ("unresolved" if unresolved else "all"),
        "clean_question": clean or (question or "").strip(),
    }
