from __future__ import annotations

"""Phase 4.5.6 — RAG foundation (retrieve → context → LLM)."""

import io

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.ai import (
    NULL_UNAVAILABLE_ANSWER,
    LLMProviderRegistry,
    NullLLMProvider,
    register_builtin_llm_providers,
)
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
from turing.services.external_reference import ExternalReferenceService
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
        return ProviderJobHandle(external_job_id="ext-rag", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Discuss renewal pricing today. Follow up next week.",
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
                NormalizedSegment(
                    sequence=1,
                    text="Follow up next week.",
                    start_ms=2000,
                    end_ms=3500,
                    confidence=0.88,
                    speaker_label="S1",
                ),
            ],
        )


def _membership(user, org, role: str) -> TuringMembership:
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture(autouse=True)
def _rag_registries():
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


@pytest.fixture
def rag_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="rag-viewer", password="pass")
    outsider = User.objects.create_user(username="rag-outsider", password="pass")
    other_org = Organization.objects.create(name="Other RAG", slug="rag-other")
    _membership(viewer, org, TuringRole.VIEWER)
    _membership(outsider, other_org, TuringRole.VIEWER)

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="rag-call.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))
    ExternalReferenceService().attach_to_transcript(
        transcript,
        external_system="salesforce",
        external_type="call",
        external_id="SF-RAG-1",
    )

    emb = LocalNeuralEmbeddingProvider(model_name="turing-local-small", dimensions=64)
    search = PgVectorSearchProvider(embedding_provider=emb)
    SearchIndexService(provider=search, embedding_provider=emb).index_transcript(
        transcript
    )
    return {
        "org": org,
        "other_org": other_org,
        "viewer": viewer,
        "outsider": outsider,
        "transcript": transcript,
        "search": search,
        "emb": emb,
    }


def test_null_llm_provider_fallback():
    assert "null" in LLMProviderRegistry.codes()
    llm = LLMProviderRegistry.create()
    assert isinstance(llm, NullLLMProvider)
    assert llm.model_name() == "null"
    gen = llm.generate([{"role": "user", "content": "hi"}], context="")
    assert gen.text == NULL_UNAVAILABLE_ANSWER
    assert gen.provider == "null"
    assert isinstance(LLMProviderRegistry.create("missing-vendor"), NullLLMProvider)


@pytest.mark.django_db
def test_retrieval_and_context_building(rag_setup):
    svc = RAGService(
        search_provider=rag_setup["search"],
        llm_provider=NullLLMProvider(),
    )
    sources = svc.retrieve_context(
        "renewal pricing",
        rag_setup["org"],
        filters={"external_system": "salesforce", "external_type": "call"},
    )
    assert sources
    top = sources[0]
    assert top["transcript_id"] == str(rag_setup["transcript"].id)
    assert top["segment_id"]
    assert top["text"]
    assert "timestamp" in top
    assert top["external_references"]

    context = svc.build_context(sources)
    assert "transcript_id:" in context
    assert "segment_id:" in context
    assert "speaker:" in context
    assert "start_ms:" in context
    assert "external_references:" in context
    assert "text:" in context
    assert "Discuss renewal" in context or "renewal" in context.lower()


@pytest.mark.django_db
def test_org_isolation(rag_setup):
    svc = RAGService(
        search_provider=rag_setup["search"],
        llm_provider=NullLLMProvider(),
    )
    foreign = svc.retrieve_context("renewal", rag_setup["other_org"])
    assert foreign == []

    payload = svc.answer("renewal", rag_setup["other_org"])
    assert payload["sources"] == []
    assert payload["provider"] == "null"
    assert payload["model_name"] == "null"
    assert payload["answer"] == NULL_UNAVAILABLE_ANSWER


@pytest.mark.django_db
def test_answer_null_provider(rag_setup):
    svc = RAGService(
        search_provider=rag_setup["search"],
        llm_provider=NullLLMProvider(),
    )
    payload = svc.answer("renewal pricing", rag_setup["org"])
    assert payload["answer"] == NULL_UNAVAILABLE_ANSWER
    assert payload["provider"] == "null"
    assert payload["model_name"] == "null"
    assert payload["sources"]
    src = payload["sources"][0]
    assert set(src.keys()) >= {
        "transcript_id",
        "segment_id",
        "score",
        "timestamp",
    }


@pytest.mark.django_db
def test_ask_api_contract(rag_setup):
    client = APIClient()
    client.force_authenticate(user=rag_setup["viewer"])
    resp = client.post(
        ASK_URL,
        {
            "question": "What was said about renewal?",
            "external_system": "salesforce",
            "external_type": "call",
            "external_id": "SF-RAG-1",
        },
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["answer"] == NULL_UNAVAILABLE_ANSWER
    assert resp.data["provider"] == "null"
    assert resp.data["model_name"] == "null"
    assert isinstance(resp.data["sources"], list)
    if resp.data["sources"]:
        src = resp.data["sources"][0]
        assert "transcript_id" in src
        assert "segment_id" in src
        assert "score" in src
        assert "timestamp" in src

    # GET also supported.
    get_resp = client.get(ASK_URL, {"question": "renewal"})
    assert get_resp.status_code == 200
    assert get_resp.data["provider"] == "null"
    assert get_resp.data["model_name"] == "null"

    # Outsider cannot see tenant sources.
    outsider = APIClient()
    outsider.force_authenticate(user=rag_setup["outsider"])
    other = outsider.post(
        ASK_URL, {"question": "renewal pricing"}, format="json"
    )
    assert other.status_code == 200
    assert other.data["sources"] == []


@pytest.mark.django_db
def test_ask_requires_question_and_auth(rag_setup):
    anon = APIClient()
    assert anon.post(ASK_URL, {"question": "x"}, format="json").status_code in {
        401,
        403,
    }

    client = APIClient()
    client.force_authenticate(user=rag_setup["viewer"])
    missing = client.post(ASK_URL, {}, format="json")
    assert missing.status_code == 400
