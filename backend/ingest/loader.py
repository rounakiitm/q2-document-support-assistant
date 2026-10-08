"""Format-dispatch document loading + structure-aware chunking (LangChain).

- PDF  -> PyPDFLoader (per-page) + heading heuristic + RecursiveCharacterTextSplitter
- DOCX -> python-docx loader that tracks Heading styles -> section paths
- MD   -> TextLoader + MarkdownHeaderTextSplitter (headers become section paths)

Every chunk is a LangChain Document carrying metadata:
    {"doc_id": ..., "section_path": "Doc Title › Section › Sub", "page": int|None}
The section_path is exactly what citations later point at, so it is kept
human-readable on purpose.
"""

import logging
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

log = logging.getLogger("ingest")

CHUNK_SIZE = 900
CHUNK_OVERLAP = 120

HEADING_RE = re.compile(r"^[A-Z][A-Za-z0-9 ,&/().'-]{2,70}$")

_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
)


def load_any(path: Path, fmt: str) -> list[Document]:
    if fmt == "pdf":
        return _load_pdf(path)
    if fmt == "docx":
        return _load_docx(path)
    if fmt == "md":
        return _load_md(path)
    raise ValueError(f"unsupported format: {fmt}")


def fmt_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {".pdf": "pdf", ".docx": "docx", ".doc": "docx", ".md": "md"}.get(
        suffix, ""
    )


# ---------------------------------------------------------------------- PDF
def _load_pdf(path: Path) -> list[Document]:
    from langchain_community.document_loaders import PyPDFLoader

    raw = PyPDFLoader(str(path)).load()
    sections: list[Document] = []
    for page_doc in raw:
        page_no = page_doc.metadata.get("page")
        page_no = page_no + 1 if isinstance(page_no, int) else None
        lines = [ln.strip() for ln in page_doc.page_content.splitlines() if ln.strip()]
        current_section = ""
        buffer: list[str] = []

        def flush() -> None:
            text = "\n".join(buffer).strip()
            if text:
                sections.append(
                    Document(
                        page_content=text,
                        metadata={"section_path": current_section, "page": page_no},
                    )
                )

        for line in lines:
            # a standalone short title-ish line starts a new section
            if len(line) < 80 and HEADING_RE.match(line) and buffer:
                flush()
                buffer = []
                current_section = line
            buffer.append(line)
        flush()
    return _subsplit(sections)


# --------------------------------------------------------------------- DOCX
def _load_docx(path: Path) -> list[Document]:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(path))
    sections: list[Document] = []
    current: list[str] = []
    heading_path: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            sections.append(
                Document(
                    page_content=text,
                    metadata={"section_path": " › ".join(heading_path)},
                    # DOCX has no pages; citations point at sections only
                )
            )

    for para in doc.paragraphs:
        style = (para.style.name or "").lower()
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("heading") or style == "title":
            flush()
            current = []
            try:
                level = int(style.split()[-1]) if style != "title" else 1
            except ValueError:
                level = 1
            heading_path = heading_path[: level - 1] + [text]
        else:
            current.append(text)
    flush()
    return _subsplit(sections)


# ----------------------------------------------------------------------- MD
def _load_md(path: Path) -> list[Document]:
    text = path.read_text(encoding="utf-8")
    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    sections = md_splitter.split_text(text)
    out: list[Document] = []
    for sec in sections:
        parts = [sec.metadata.get(k, "") for k in ("h1", "h2", "h3")]
        section_path = " › ".join(p for p in parts if p)
        out.append(
            Document(page_content=sec.page_content, metadata={"section_path": section_path})
        )
    return _subsplit(out)


# ------------------------------------------------------------------- shared
def _subsplit(sections: list[Document]) -> list[Document]:
    chunks: list[Document] = []
    for sec in sections:
        pieces = _SPLITTER.split_text(sec.page_content)
        for piece in pieces:
            chunks.append(
                Document(
                    page_content=piece,
                    metadata={
                        "section_path": sec.metadata.get("section_path", ""),
                        "page": sec.metadata.get("page"),
                    },
                )
            )
    if not chunks:
        log.warning("no chunks produced")
    return chunks