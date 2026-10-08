"""Shared fixtures: temp storage paths, fake LLM, fake KB, real DB."""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="q2_test_"))
# pydantic-settings reads these env vars before backend.config is imported
for var, val in {
    "DB_PATH": str(_TMP / "test.db"),
    "UPLOADS_DIR": str(_TMP / "uploads"),
    "CHROMA_DIR": str(_TMP / "chroma"),
    "SEED_DOCS_DIR": str(_TMP / "seed_docs"),
    # genai.Client validates key presence at construction; tests never call it
    "GEMINI_API_KEY": "fake-key-for-tests",
}.items():
    os.environ[var] = val

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.genai import types  # noqa: E402


class FakeLLM:
    """Scripted LLM: each generate() pops one step; steps are dicts of tool calls."""

    def __init__(self, script: list[dict] | None = None, json_responses: list[dict] | None = None):
        self.script = list(script or [])
        self.json_responses = list(json_responses or [])

    def generate(self, contents, config=None):  # noqa: ARG002
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        step = self.script.pop(0)
        if step.get("text_only"):
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(
                            role="model", parts=[types.Part(text=step["text_only"])]
                        )
                    )
                ]
            )
        parts = [
            types.Part(function_call=types.FunctionCall(name=c["name"], args=c.get("args") or {}))
            for c in step["calls"]
        ]
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
        )

    def json_call(self, prompt: str, system: str = "", max_tokens: int = 700):  # noqa: ARG002
        if self.json_responses:
            return self.json_responses.pop(0)
        return {}

    def search(self, query: str) -> tuple[str, list[dict]]:
        return f"web summary for: {query} (outside the knowledge base)", [
            {"title": "example.gov", "uri": "https://example.gov/x"}
        ]


class FakeEmbeddings:
    """Deterministic bag-of-words embeddings; no network."""

    def embed_query(self, text: str) -> list[float]:
        import hashlib

        seed = int(hashlib.md5(text.casefold().encode()).hexdigest()[:8], 16)
        return [((seed >> (i % 16)) & 1) * 0.5 + 0.1 for i in range(8)]


@pytest.fixture()
def tmp_db():
    from backend.storage.db import DB

    return DB(_TMP / f"unit_{uuid.uuid4().hex}.db")