from __future__ import annotations

"""Phase 4.5.5 — EmbeddingProvider abstraction + local neural embedder."""

import io

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import TuringRole, UseCase
from turing.models import Embedding, Organization, TuringMembership
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.search.embeddings import (
    EmbeddingProviderRegistry,
    LocalNeuralEmbeddingProvider,
    NullEmbeddingProvider,
    register_builtin_embedding_providers,
)
from turing.search.embeddings.local import DEFAULT_MODEL
from turing.search import (
    PgVectorSearchProvider,
    SemanticSearchRegistry,
    register_builtin_search_providers,
)
from turing.search.embedder import cosine_similarity
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.search_index import SearchIndexService
from turing.services.transcription import TranscriptionService

User = get_user_model()


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-emb", provider_code=self.code)

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


@pytest.fixture(autouse=True)
def _embedding_registries():
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()
    yield
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()


def test_provider_registry():
    assert "local" in EmbeddingProviderRegistry.codes()
    assert "null" in EmbeddingProviderRegistry.codes()
    assert isinstance(EmbeddingProviderRegistry.create(), LocalNeuralEmbeddingProvider)
    assert isinstance(EmbeddingProviderRegistry.create("null"), NullEmbeddingProvider)
    assert isinstance(
        EmbeddingProviderRegistry.create("local"), LocalNeuralEmbeddingProvider
    )


def test_fallback_unknown_provider():
    provider = EmbeddingProviderRegistry.create("does-not-exist")
    assert isinstance(provider, NullEmbeddingProvider)
    assert provider.embed("hello") == []
    assert provider.dimensions() == 0


def test_deterministic_embeddings():
    a = LocalNeuralEmbeddingProvider(model_name=DEFAULT_MODEL, dimensions=64)
    b = LocalNeuralEmbeddingProvider(model_name=DEFAULT_MODEL, dimensions=64)
    v1 = a.embed("renewal pricing")
    v2 = b.embed("renewal pricing")
    assert v1 == v2
    assert len(v1) == 64
    assert a.dimensions() == 64
    assert a.model_name() == DEFAULT_MODEL


def test_dimensions_compatibility_and_ranking():
    provider = LocalNeuralEmbeddingProvider(
        model_name="turing-local-small", dimensions=64
    )
    doc = provider.embed("Discuss renewal pricing today.")
    query = provider.embed("renewal")
    other = provider.embed("weather forecast tomorrow")
    assert len(doc) == provider.dimensions() == 64
    assert cosine_similarity(doc, query) > cosine_similarity(doc, other)

    # Different model_name → different space (not required equal dims).
    other_model = LocalNeuralEmbeddingProvider(
        model_name="custom-model-x", dimensions=64
    )
    assert other_model.embed("renewal") != provider.embed("renewal")


@pytest.mark.django_db
def test_search_indexing_integration(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    user = User.objects.create_user(username="emb-user", password="pass")
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="emb-call.wav",
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
    count = SearchIndexService(
        provider=search, embedding_provider=emb
    ).index_transcript(transcript)
    assert count == 2

    rows = list(Embedding.objects.filter(organization=org))
    assert len(rows) == 2
    for row in rows:
        assert row.provider == "local"
        assert row.model_name == "turing-local-small"
        assert len(row.vector) == 64
        assert row.dimensions == 64

    result = search.search("renewal", organization_id=org.id, limit=5)
    assert result.provider == "pgvector"
    assert result.hits
    assert result.hits[0].score > 0
    assert "transcript_id" in result.hits[0].metadata


@pytest.mark.django_db
def test_fallback_null_embedding_indexing(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="emb-null.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))

    emb = NullEmbeddingProvider()
    search = PgVectorSearchProvider(embedding_provider=emb)
    SearchIndexService(provider=search, embedding_provider=emb).index_transcript(
        transcript
    )
    row = Embedding.objects.filter(organization=org).first()
    assert row is not None
    assert row.provider == "null"
    assert row.vector == []
    assert search.search("renewal", organization_id=org.id).hits == []
