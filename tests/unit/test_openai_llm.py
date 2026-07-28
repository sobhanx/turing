from __future__ import annotations

"""Phase 4.5.7 — OpenAI LLM provider + RAG provider metadata."""

import io
import json
from unittest.mock import MagicMock, patch
from urllib import error

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.ai import (
    NULL_UNAVAILABLE_ANSWER,
    LLMProviderRegistry,
    NullLLMProvider,
    OpenAILLMProvider,
    register_builtin_llm_providers,
)
from turing.ai.base import LLMGeneration, LLMMessage
from turing.ai.exceptions import LLMProviderConfigurationError, LLMProviderError
from turing.domain.enums import TuringRole, UseCase
from turing.models import Organization, TuringMembership
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.search import (
    PgVectorSearchProvider,
    SemanticSearchRegistry,
    register_builtin_search_providers,
)
from turing.search.embeddings import (
    LocalNeuralEmbeddingProvider,
    register_builtin_embedding_providers,
)
from turing.search.embeddings.registry import EmbeddingProviderRegistry
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.rag import RAGService
from turing.services.search_index import SearchIndexService
from turing.services.transcription import TranscriptionService

User = get_user_model()
ASK_URL = "/api/turing/v1/speech-center/ask/"


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-llm", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Discuss renewal pricing today.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Discuss renewal pricing today.",
                    start_ms=0,
                    end_ms=2000,
                    confidence=0.9,
                    speaker_label="S1",
                ),
            ],
        )


@pytest.fixture(autouse=True)
def _llm_registries():
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()
    LLMProviderRegistry.clear()
    register_builtin_llm_providers()
    yield
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()
    LLMProviderRegistry.clear()
    register_builtin_llm_providers()


def test_registry_selection_openai(settings):
    assert "openai" in LLMProviderRegistry.codes()
    assert "null" in LLMProviderRegistry.codes()
    settings.TURING_LLM_PROVIDER = "openai"
    LLMProviderRegistry.clear()
    register_builtin_llm_providers()
    provider = LLMProviderRegistry.create()
    assert isinstance(provider, OpenAILLMProvider)
    assert provider.model_name()


def test_openai_generate_mocked(settings):
    settings.TURING_OPENAI_API_KEY = "sk-test-not-real"
    settings.TURING_LLM_MODEL = "gpt-4o-mini"
    provider = OpenAILLMProvider(api_key="sk-test-not-real", model_name="gpt-4o-mini")

    fake_payload = {
        "choices": [
            {"message": {"content": "Renewal pricing was discussed."}}
        ]
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps(fake_payload).encode("utf-8")
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("turing.ai.providers.openai.request.urlopen", return_value=fake_resp) as mocked:
        result = provider.generate(
            [LLMMessage(role="user", content="What about renewal?")],
            context="transcript_id: t1\ntext: Discuss renewal pricing today.",
        )

    assert result.text == "Renewal pricing was discussed."
    assert result.provider == "openai"
    assert result.model_name == "gpt-4o-mini"
    mocked.assert_called_once()
    # Request must not leak into assertions as logged content; verify Authorization
    # header exists but do not print the key.
    req = mocked.call_args[0][0]
    assert "Authorization" in req.headers
    assert req.headers["Authorization"].startswith("Bearer ")


def test_openai_missing_key_raises(settings):
    settings.TURING_OPENAI_API_KEY = ""
    provider = OpenAILLMProvider(api_key="", model_name="gpt-4o-mini")
    with pytest.raises(LLMProviderConfigurationError):
        provider.generate([{"role": "user", "content": "hi"}], context="")


def test_openai_http_error_raises(settings):
    settings.TURING_OPENAI_API_KEY = "sk-test"
    provider = OpenAILLMProvider(api_key="sk-test", model_name="gpt-4o-mini")
    http_err = error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=500,
        msg="err",
        hdrs=None,
        fp=io.BytesIO(b"secret body"),
    )
    with patch("turing.ai.providers.openai.request.urlopen", side_effect=http_err):
        with pytest.raises(LLMProviderError, match="HTTP 500"):
            provider.generate([{"role": "user", "content": "hi"}], context="pii")


@pytest.mark.django_db
def test_rag_fallback_when_provider_unavailable(db, monkeypatch, settings):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    settings.TURING_OPENAI_API_KEY = "sk-test"
    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="llm-fallback.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))

    emb = LocalNeuralEmbeddingProvider(model_name="turing-local-small", dimensions=64)
    search = PgVectorSearchProvider(embedding_provider=emb)
    SearchIndexService(provider=search, embedding_provider=emb).index_transcript(
        transcript
    )

    class _BoomLLM(OpenAILLMProvider):
        def generate(self, messages, context=None):
            raise LLMProviderError("simulated outage")

    svc = RAGService(
        search_provider=search,
        llm_provider=_BoomLLM(api_key="sk-test", model_name="gpt-4o-mini"),
    )
    payload = svc.answer("renewal", org)
    assert payload["answer"] == NULL_UNAVAILABLE_ANSWER
    assert payload["provider"] == "null"
    assert payload["model_name"] == "null"
    assert "sources" in payload


@pytest.mark.django_db
def test_rag_response_includes_provider_metadata(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="llm-viewer", password="pass")
    TuringMembership.objects.create(
        user=viewer, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="llm-meta.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))
    emb = LocalNeuralEmbeddingProvider(model_name="turing-local-small", dimensions=64)
    search = PgVectorSearchProvider(embedding_provider=emb)
    SearchIndexService(provider=search, embedding_provider=emb).index_transcript(
        transcript
    )

    class _StubLLM(OpenAILLMProvider):
        def generate(self, messages, context=None):
            return LLMGeneration(
                text="Stub grounded answer about renewal.",
                model_name="gpt-4o-mini",
                provider="openai",
            )

    svc = RAGService(
        search_provider=search,
        llm_provider=_StubLLM(api_key="sk-test", model_name="gpt-4o-mini"),
    )
    payload = svc.answer("renewal", org)
    assert payload["answer"].startswith("Stub grounded")
    assert payload["provider"] == "openai"
    assert payload["model_name"] == "gpt-4o-mini"
    assert isinstance(payload["sources"], list)

    client = APIClient()
    client.force_authenticate(user=viewer)
    with patch.object(
        RAGService,
        "answer",
        return_value={
            "answer": "API answer",
            "sources": [],
            "provider": "openai",
            "model_name": "gpt-4o-mini",
        },
    ):
        resp = client.post(ASK_URL, {"question": "renewal?"}, format="json")
    assert resp.status_code == 200
    assert resp.data["provider"] == "openai"
    assert resp.data["model_name"] == "gpt-4o-mini"
    assert "answer" in resp.data
    assert "sources" in resp.data
