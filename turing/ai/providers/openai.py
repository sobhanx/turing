from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence
from urllib import error, request

from django.conf import settings

from turing.ai.base import LLMGeneration, LLMMessage, LLMProvider
from turing.ai.exceptions import LLMProviderConfigurationError, LLMProviderError
from turing.ai.interfaces import AIProvider
from turing.ai.registry import AIProviderRegistry, LLMProviderRegistry
from turing.ai.types import AnalysisResult, TranscriptInput
from turing.conf import get_turing_settings
from turing.domain.enums import AnalysisType
from turing.domain.exceptions import ProviderError

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

MAX_SUITE_TOPICS = 8
MAX_SUITE_ACTION_ITEMS = 10

SUITE_SYSTEM_PROMPT = (
    "Analyze this transcript. Return JSON only (no markdown, no prose outside JSON).\n\n"
    "Schema (exact top-level keys):\n"
    "{\n"
    '  "summary": {"summary": "...", "main_points": []},\n'
    '  "action_items": [{"task": "...", "owner": null, "deadline": null}],\n'
    '  "topics": []\n'
    "}\n\n"
    "summary.summary:\n"
    "- maximum 5 sentences\n"
    "- include main context and important outcomes/decisions\n"
    "- avoid repetition, filler, and unnecessary background\n\n"
    "summary.main_points:\n"
    "- maximum 5 items\n"
    "- each item is one concise sentence\n\n"
    "action_items:\n"
    f"- maximum {MAX_SUITE_ACTION_ITEMS} items\n"
    "- only actionable tasks\n"
    '- each item: "task" (string), "owner" (string or null), '
    '"deadline" (string or null)\n'
    "- put the most important tasks first\n\n"
    "topics:\n"
    f"- maximum {MAX_SUITE_TOPICS} short labels\n"
    "- put the most relevant topics first\n\n"
    "Use the same language as the transcript (including Persian when applicable)."
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (Latin + common Persian terminators)."""
    body = (text or "").strip()
    if not body:
        return []
    parts = re.split(r"(?<=[.!?؟۔])\s+", body)
    return [part.strip() for part in parts if part.strip()]


def _limit_sentences(text: str, *, max_sentences: int = 5) -> str:
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return (text or "").strip()
    return " ".join(sentences[:max_sentences]).strip()


def _limit_main_points(points: list[Any], *, max_items: int = 5) -> list[str]:
    limited: list[str] = []
    for item in points:
        sentence = _limit_sentences(str(item), max_sentences=1)
        if sentence:
            limited.append(sentence)
        if len(limited) >= max_items:
            break
    return limited


def _limit_topics(topics: list[Any], *, max_items: int = MAX_SUITE_TOPICS) -> list[str]:
    """Keep the first (most relevant) topics; drop empties and extras."""
    limited: list[str] = []
    seen: set[str] = set()
    for item in topics:
        topic = str(item).strip()
        if not topic:
            continue
        key = topic.casefold()
        if key in seen:
            continue
        seen.add(key)
        limited.append(topic)
        if len(limited) >= max_items:
            break
    return limited


def _limit_action_items(
    items: list[Any], *, max_items: int = MAX_SUITE_ACTION_ITEMS
) -> list[dict[str, Any]]:
    """Keep the first (most relevant) action items; drop invalid/extra entries."""
    limited: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        limited.append(
            {
                "task": task,
                "owner": item.get("owner"),
                "deadline": item.get("deadline"),
            }
        )
        if len(limited) >= max_items:
            break
    return limited


def _openai_base_url() -> str:
    """Return OpenAI-compatible API base URL (no trailing slash)."""
    raw = (
        getattr(settings, "TURING_OPENAI_BASE_URL", None)
        or getattr(get_turing_settings(), "openai_base_url", None)
        or DEFAULT_OPENAI_BASE_URL
    )
    return str(raw).strip().rstrip("/") or DEFAULT_OPENAI_BASE_URL


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

    def analyze_suite(
        self, transcript: TranscriptInput
    ) -> dict[str, AnalysisResult]:
        """One JSON-mode chat completion covering summary, action_items, and topics."""
        payload = self._chat_json(
            system=SUITE_SYSTEM_PROMPT,
            user=self._transcript_body(transcript),
            timeout=120,
        )
        try:
            summary_raw = payload.get("summary")
            actions_raw = payload.get("action_items")
            topics_raw = payload.get("topics")
            if not isinstance(summary_raw, dict):
                raise ProviderError(
                    "Suite response missing summary object.",
                    code="PROVIDER_RESPONSE",
                    retryable=False,
                )
            if not isinstance(actions_raw, list):
                raise ProviderError(
                    "Suite response missing action_items array.",
                    code="PROVIDER_RESPONSE",
                    retryable=False,
                )
            if not isinstance(topics_raw, list):
                raise ProviderError(
                    "Suite response missing topics array.",
                    code="PROVIDER_RESPONSE",
                    retryable=False,
                )
            model = self._model_name()
            summary_text = _limit_sentences(str(summary_raw.get("summary", "")))
            main_points = _limit_main_points(list(summary_raw.get("main_points") or []))
            return {
                AnalysisType.SUMMARY: AnalysisResult(
                    content={
                        "summary": summary_text,
                        "main_points": main_points,
                    },
                    model_name=model,
                ),
                AnalysisType.ACTION_ITEMS: AnalysisResult(
                    content=_limit_action_items(list(actions_raw)),
                    model_name=model,
                ),
                AnalysisType.TOPICS: AnalysisResult(
                    content=_limit_topics(list(topics_raw)),
                    model_name=model,
                ),
            }
        except ProviderError:
            raise
        except (TypeError, ValueError) as exc:
            logger.warning(
                "OpenAI suite JSON malformed (body redacted) error=%s",
                type(exc).__name__,
            )
            raise ProviderError(
                "OpenAI suite response could not be parsed.",
                code="PROVIDER_RESPONSE",
                retryable=False,
            ) from exc

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

    def _chat_json(
        self, *, system: str, user: str, timeout: float = 60
    ) -> dict[str, Any]:
        model = self._model_name()
        endpoint = f"{_openai_base_url()}/chat/completions"
        key_configured = bool((get_turing_settings().openai_api_key or "").strip())
        body = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        logger.warning(
            "[AI-DEBUG] openai_request provider=%s endpoint=%s model=%s key_configured=%s",
            self.__class__.__name__,
            endpoint,
            model,
            key_configured,
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                payload = json.loads(response.read().decode("utf-8"))
                logger.warning(
                    "[AI-DEBUG] openai_success status=%s model=%s",
                    status,
                    model,
                )
        except error.HTTPError as exc:
            logger.warning(
                "[AI-DEBUG] openai_failed status=%s model=%s endpoint=%s",
                exc.code,
                model,
                endpoint,
            )
            # Do not log response body (may include prompt echoes).
            raise ProviderError(
                f"OpenAI request failed: HTTP {exc.code}",
                code="PROVIDER_RESPONSE",
                retryable=exc.code >= 500,
            ) from exc
        except error.URLError as exc:
            logger.warning(
                "[AI-DEBUG] openai_failed network_error=%s model=%s endpoint=%s",
                type(exc.reason).__name__,
                model,
                endpoint,
            )
            raise ProviderError(
                f"OpenAI network error: {exc.reason}",
                code="PROVIDER_NETWORK",
                retryable=True,
            ) from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            logger.warning(
                "Unexpected OpenAI response shape/JSON (keys redacted) error=%s",
                type(exc).__name__,
            )
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

        endpoint = f"{_openai_base_url()}/chat/completions"
        body = {
            "model": self._model,
            "messages": chat_messages,
        }
        req = request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # TEMP debug — never log API key or prompt/transcript content
        logger.warning(
            "[AI-DEBUG] openai_request provider=%s endpoint=%s model=%s key_configured=%s",
            self.__class__.__name__,
            endpoint,
            self._model,
            True,
        )
        try:
            with request.urlopen(req, timeout=self._timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                payload = json.loads(response.read().decode("utf-8"))
                logger.warning(
                    "[AI-DEBUG] openai_success status=%s model=%s",
                    status,
                    self._model,
                )
        except error.HTTPError as exc:
            logger.warning(
                "[AI-DEBUG] openai_failed status=%s model=%s endpoint=%s",
                exc.code,
                self._model,
                endpoint,
            )
            raise LLMProviderError(
                f"OpenAI LLM request failed: HTTP {exc.code}"
            ) from exc
        except error.URLError as exc:
            logger.warning(
                "[AI-DEBUG] openai_failed network_error=%s model=%s endpoint=%s",
                type(exc.reason).__name__,
                self._model,
                endpoint,
            )
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
