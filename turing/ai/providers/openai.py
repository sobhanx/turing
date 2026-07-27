from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error, request

from turing.ai.interfaces import AIProvider
from turing.ai.registry import AIProviderRegistry
from turing.ai.types import AnalysisResult, TranscriptInput
from turing.conf import get_turing_settings
from turing.domain.exceptions import ProviderError

logger = logging.getLogger(__name__)


@AIProviderRegistry.register
class OpenAIProvider(AIProvider):
    """
    Optional OpenAI-backed provider.

    Not required for tests or local development — use ``fake`` instead.
  """

    code = "openai"

    def summarize(self, transcript: TranscriptInput) -> AnalysisResult:
        payload = self._chat_json(
            system=(
                "Return JSON with keys summary (string) and main_points (array of strings)."
            ),
            user=self._transcript_body(transcript),
        )
        return AnalysisResult(
            content={
                "summary": str(payload.get("summary", "")),
                "main_points": list(payload.get("main_points") or []),
            },
            model_name=self._model_name(),
        )

    def extract_action_items(self, transcript: TranscriptInput) -> AnalysisResult:
        payload = self._chat_json(
            system=(
                "Return JSON with key items: array of objects with task (string), "
                "owner (string or null), deadline (string or null)."
            ),
            user=self._transcript_body(transcript),
        )
        return AnalysisResult(
            content=list(payload.get("items") or []),
            model_name=self._model_name(),
        )

    def extract_topics(self, transcript: TranscriptInput) -> AnalysisResult:
        payload = self._chat_json(
            system="Return JSON with key topics: array of short topic strings.",
            user=self._transcript_body(transcript),
        )
        topics = payload.get("topics")
        if isinstance(topics, list):
            normalized = [str(item) for item in topics]
        else:
            normalized = []
        return AnalysisResult(content=normalized, model_name=self._model_name())

    def _model_name(self) -> str:
        return get_turing_settings().openai_model or "gpt-4o-mini"

    def _api_key(self) -> str:
        key = (get_turing_settings().openai_api_key or "").strip()
        if not key:
            raise ProviderError(
                "OpenAI API key is not configured.",
                code="CONFIGURATION",
                retryable=False,
            )
        return key

    def _transcript_body(self, transcript: TranscriptInput) -> str:
        if transcript.full_text:
            return transcript.full_text
        return "\n".join(segment.text for segment in transcript.segments if segment.text)

    def _chat_json(self, *, system: str, user: str) -> dict[str, Any]:
        model = self._model_name()
        body = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(
                f"OpenAI request failed: {detail}",
                code="PROVIDER_RESPONSE",
                retryable=exc.code >= 500,
            ) from exc
        except error.URLError as exc:
            raise ProviderError(
                f"OpenAI network error: {exc.reason}",
                code="PROVIDER_NETWORK",
                retryable=True,
            ) from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("Unexpected OpenAI response shape: %s", payload)
            raise ProviderError(
                "OpenAI returned an unexpected response.",
                code="PROVIDER_RESPONSE",
                retryable=False,
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderError(
                "OpenAI JSON response must be an object.",
                code="PROVIDER_RESPONSE",
                retryable=False,
            )
        return parsed
