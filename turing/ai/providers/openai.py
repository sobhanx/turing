from __future__ import annotations

import json
import logging
from typing import Any, Sequence
from urllib import error, request

from django.conf import settings

from turing.ai.base import LLMGeneration, LLMMessage, LLMProvider
from turing.ai.exceptions import LLMProviderConfigurationError, LLMProviderError
from turing.ai.interfaces import AIProvider
from turing.ai.registry import AIProviderRegistry, LLMProviderRegistry
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
            # Do not log response body (may include prompt echoes).
            raise ProviderError(
                f"OpenAI request failed: HTTP {exc.code}",
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
            logger.warning("Unexpected OpenAI response shape (keys redacted)")
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


def _llm_model_name(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    return (
        getattr(settings, "TURING_LLM_MODEL", None)
        or getattr(settings, "TURING_OPENAI_MODEL", None)
        or "gpt-4o-mini"
    ).strip() or "gpt-4o-mini"


def _llm_api_key(explicit: str | None = None) -> str:
    if explicit is not None:
        return explicit.strip()
    return (
        getattr(settings, "TURING_OPENAI_API_KEY", None)
        or get_turing_settings().openai_api_key
        or ""
    ).strip()


def _normalize_messages(
    messages: Sequence[LLMMessage] | Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in messages:
        if isinstance(item, LLMMessage):
            normalized.append({"role": item.role, "content": item.content})
        elif isinstance(item, dict):
            normalized.append(
                {
                    "role": str(item.get("role") or "user"),
                    "content": str(item.get("content") or ""),
                }
            )
    return normalized


def _context_to_text(context: str | dict[str, Any] | None) -> str:
    if context is None:
        return ""
    if isinstance(context, str):
        return context.strip()
    if isinstance(context, dict):
        return str(context.get("text") or context.get("context") or "").strip()
    return str(context).strip()


@LLMProviderRegistry.register
class OpenAILLMProvider(LLMProvider):
    """
    Production OpenAI chat completions provider for RAG (Phase 4.5.7).

    Separate from transcript-intelligence ``OpenAIProvider``. Never logs API
    keys or prompt/context content (may contain transcript PII).
    """

    code = "openai"
    display_name = "OpenAI"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._model = _llm_model_name(model_name)
        self._api_key_override = api_key
        self._timeout = float(timeout_seconds)

    def model_name(self) -> str:
        return self._model

    def generate(
        self,
        messages: Sequence[LLMMessage] | Sequence[dict[str, str]],
        context: str | dict[str, Any] | None = None,
    ) -> LLMGeneration:
        api_key = _llm_api_key(self._api_key_override)
        if not api_key:
            raise LLMProviderConfigurationError(
                "OpenAI API key is not configured for the LLM provider."
            )

        chat_messages = _normalize_messages(messages)
        context_text = _context_to_text(context)
        if context_text:
            grounding = (
                "Use only the following Speech Center context when answering. "
                "If it is insufficient, say you do not have enough information.\n\n"
                f"{context_text}"
            )
            chat_messages = [
                {"role": "system", "content": grounding},
                *chat_messages,
            ]

        body = {
            "model": self._model,
            "messages": chat_messages,
        }
        req = request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            logger.warning(
                "OpenAI LLM HTTP error status=%s (body redacted)",
                exc.code,
            )
            raise LLMProviderError(
                f"OpenAI LLM request failed: HTTP {exc.code}"
            ) from exc
        except error.URLError as exc:
            logger.warning("OpenAI LLM network error (details redacted)")
            raise LLMProviderError(
                f"OpenAI LLM network error: {type(exc.reason).__name__}"
            ) from exc

        try:
            text = str(payload["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("OpenAI LLM unexpected response shape (redacted)")
            raise LLMProviderError(
                "OpenAI LLM returned an unexpected response."
            ) from exc

        return LLMGeneration(
            text=text,
            model_name=self.model_name(),
            provider=self.code,
            details={"available": True},
        )
