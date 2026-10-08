from typing import Any

from pydantic import BaseModel, Field


class SessionInfo(BaseModel):
    session_id: str


class ChatMessageIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class Citation(BaseModel):
    doc: str
    section: str = ""
    span_text: str
    valid: bool = True


class Source(BaseModel):
    title: str = ""
    uri: str = ""


class AnswerOut(BaseModel):
    session_id: str
    turn: int = 0
    answer: str
    citations: list[Citation] = []
    citations_valid: bool = True
    not_in_kb: bool = False
    compressed: bool = False  # answer reuses an earlier turn instead of repeating
    topic_switch_ack: bool = False  # agent acknowledged a topic switch
    assumptions: list[str] = []
    web_sources: list[Source] = []
    tool_runs: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    memory: dict[str, Any] = {}


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    fmt: str
    n_chunks: int = 0
    n_sections: int = 0
    uploaded_at: str = ""
    mention: str = ""  # @alias for scoping retrieval to this document