"""Gemini client wrapper: retries with backoff + token accounting + JSON helper."""

import json
import logging
import re
import time

from google import genai
from google.genai import types

from backend.config import settings

log = logging.getLogger("llm")

_RETRYABLE = ("429", "RESOURCE_EXHAUSTED", "500", "503", "UNAVAILABLE")


class LLMClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.model = model or settings.model_name
        self._key_index = 0
        self._keys = [api_key] if api_key else (settings.api_keys or [""])
        self._client = self._make_client()
        self.last_usage: dict[str, int] = {}

    def _make_client(self) -> genai.Client:
        return genai.Client(api_key=self._keys[self._key_index])

    def _rotate_key(self) -> bool:
        """Move to the next key in the pool. Returns False when exhausted."""
        if len(self._keys) < 2 or self._key_index >= len(self._keys) - 1:
            return False
        self._key_index += 1
        self._client = self._make_client()
        log.warning("rotated to API key %d/%d after quota error", self._key_index + 1, len(self._keys))
        return True

    # ------------------------------------------------------------------ core
    def _attempt(self, fn):
        delays = [1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 45.0, 60.0]  # spans two rpm windows
        last = "unknown error"
        for attempt, delay in enumerate(delays):
            try:
                return fn()
            except Exception as exc:
                last = str(exc)
                if any(tag in last for tag in _RETRYABLE):
                    if "429" in last or "RESOURCE_EXHAUSTED" in last:
                        if self._rotate_key():
                            time.sleep(1.0)
                            continue  # fresh key: retry immediately on the same delay slot
                    log.warning(
                        "llm retry %d/%d after error: %s",
                        attempt + 1,
                        len(delays),
                        last[:150],
                    )
                    time.sleep(delay)
                    continue
                if "API key not valid" in last or "API_KEY_INVALID" in last:
                    # dead key in the pool: drop it from rotation immediately
                    if self._rotate_key():
                        continue
                raise
        raise RuntimeError(f"LLM failed after retries: {last[:200]}")

    def _account(self, resp) -> dict:
        usage = getattr(resp, "usage_metadata", None)
        if not usage:
            self.last_usage = {}
            return self.last_usage
        self.last_usage = {
            "prompt_tokens": usage.prompt_token_count or 0,
            "completion_tokens": usage.candidates_token_count or 0,
            "total_tokens": usage.total_token_count or 0,
        }
        return self.last_usage

    def generate(self, contents, config=None):
        resp = self._attempt(
            lambda: self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        )
        self._account(resp)
        return resp

    def json_call(self, prompt: str, system: str = "", max_tokens: int = 700) -> dict:
        """Small structured-output helper (memory pre-pass, summaries)."""
        cfg = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=0.0,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        )
        resp = self._attempt(
            lambda: self._client.models.generate_content(
                model=self.model, contents=prompt, config=cfg
            )
        )
        self._account(resp)
        return _parse_json(resp.text or "")

    def search(self, query: str) -> tuple[str, list[dict]]:
        """Grounded web lookup. Returns (summary_text, sources)."""
        cfg = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.1,
        )
        resp = self._attempt(
            lambda: self._client.models.generate_content(
                model=self.model, contents=query, config=cfg
            )
        )
        self._account(resp)
        text = resp.text or ""
        sources: list[dict] = []
        candidates = getattr(resp, "candidates", None)
        if candidates:
            meta = getattr(candidates[0], "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web and (web.uri or web.title):
                    sources.append({"title": web.title or "", "uri": web.uri or ""})
        return text, sources


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {}