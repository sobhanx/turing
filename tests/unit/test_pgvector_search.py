from __future__ import annotations

"""Phase 4.5.4 — PgVectorSearchProvider + ranking API."""

import io

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.domain.enums import TuringRole, UseCase
from turing.models import Embedding, Organization, TuringMembership
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.search import (
    NullSemanticSearchProvider,
    PgVectorSearchProvider,
    SemanticSearchRegistry,
    register_builtin_search_providers,
)
from turing.search.base import SearchDocument
from turing.search.embedder import cosine_similarity, embed_text
from turing.search.embeddings import register_builtin_embedding_providers
from turing.search.embeddings.registry import EmbeddingProviderRegistry
from turing.services.external_reference import ExternalReferenceService
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.search_index import SearchIndexService
from turing.services.transcription import TranscriptionService

User = get_user_model()
SEARCH_URL = "/api/turing/v1/search/"


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-pgv", provider_code=self.code)

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
def _pgvector_registry():
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()
    yield
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()


@pytest.fixture
def pgv_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="pgv-viewer", password="pass")
    outsider = User.objects.create_user(username="pgv-outsider", password="pass")
    other_org = Organization.objects.create(name="Other PgV", slug="pgv-other")
    _membership(viewer, org, TuringRole.VIEWER)
    _membership(outsider, other_org, TuringRole.VIEWER)

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="pgv-call.wav",
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
        external_id="SF-PGV-1",
    )
    return {
        "org": org,
        "other_org": other_org,
        "viewer": viewer,
        "outsider": outsider,
        "transcript": transcript,
        "media": media,
    }


def test_provider_selection():
    assert "pgvector" in SemanticSearchRegistry.codes()
    assert "null" in SemanticSearchRegistry.codes()
    assert isinstance(SemanticSearchRegistry.create(), PgVectorSearchProvider)
    assert isinstance(SemanticSearchRegistry.create("null"), NullSemanticSearchProvider)
    assert isinstance(
        SemanticSearchRegistry.create("pgvector"), PgVectorSearchProvider
    )


def test_embedder_cosine_ranking():
    a = embed_text("renewal pricing contract", dimensions=64)
    b = embed_text("renewal pricing discussion", dimensions=64)
    c = embed_text("weather forecast tomorrow", dimensions=64)
    assert cosine_similarity(a, b) > cosine_similarity(a, c)


@pytest.mark.django_db
def test_pgvector_indexing(pgv_setup):
    transcript = pgv_setup["transcript"]
    provider = PgVectorSearchProvider(dimensions=64)
    count = SearchIndexService(provider=provider).index_transcript(transcript)
    assert count == 2
    rows = list(Embedding.objects.filter(organization=pgv_setup["org"]))
    assert len(rows) == 2
    for row in rows:
        assert isinstance(row.vector, list)
        assert len(row.vector) == 64
        assert row.dimensions == 64
        assert row.provider == "local"
        assert row.model_name
        assert row.content_hash
        assert row.metadata["text"]
        assert row.metadata["external_references"]


@pytest.mark.django_db
def test_duplicate_hash_skips_reembed(pgv_setup):
    transcript = pgv_setup["transcript"]
    provider = PgVectorSearchProvider(dimensions=64)
    svc = SearchIndexService(provider=provider)
    assert svc.index_transcript(transcript) == 2
    row = Embedding.objects.filter(organization=pgv_setup["org"]).first()
    original_vector = list(row.vector)
    original_hash = row.content_hash
    row_id = row.id

    # Second pass: same text → metadata refresh only, vector unchanged.
    assert svc.index_transcript(transcript) == 2
    row.refresh_from_db()
    assert row.id == row_id
    assert row.content_hash == original_hash
    assert row.vector == original_vector
    assert Embedding.objects.filter(organization=pgv_setup["org"]).count() == 2


@pytest.mark.django_db
def test_ranking_and_api(pgv_setup):
    transcript = pgv_setup["transcript"]
    SearchIndexService(provider=PgVectorSearchProvider(dimensions=128)).index_transcript(
        transcript
    )

    client = APIClient()
    client.force_authenticate(user=pgv_setup["viewer"])
    resp = client.get(
        SEARCH_URL,
        {
            "q": "renewal pricing",
            "external_system": "salesforce",
            "external_type": "call",
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    assert resp.data["provider"] == "pgvector"
    results = resp.data["results"]
    assert results
    top = results[0]
    assert top["transcript_id"] == str(transcript.id)
    assert top["segment_id"]
    assert "renewal" in top["text"].lower()
    assert top["score"] > 0
    assert top["start_time"] is not None
    assert top["end_time"] is not None
    assert top["external_references"]
    # Renewal segment should outrank the follow-up segment.
    if len(results) > 1:
        assert results[0]["score"] >= results[1]["score"]


@pytest.mark.django_db
def test_org_isolation_no_cross_tenant(pgv_setup):
    transcript = pgv_setup["transcript"]
    SearchIndexService(provider=PgVectorSearchProvider(dimensions=64)).index_transcript(
        transcript
    )

    # Direct provider search for other org must not see rows.
    provider = PgVectorSearchProvider(dimensions=64)
    foreign = provider.search(
        "renewal",
        organization_id=pgv_setup["other_org"].id,
        limit=10,
    )
    assert foreign.hits == []
    assert foreign.provider == "pgvector"

    own = provider.search(
        "renewal",
        organization_id=pgv_setup["org"].id,
        limit=10,
    )
    assert own.hits

    outsider = APIClient()
    outsider.force_authenticate(user=pgv_setup["outsider"])
    resp = outsider.get(SEARCH_URL, {"q": "renewal"})
    assert resp.status_code == 200
    assert resp.data["provider"] == "pgvector"
    assert resp.data["results"] == []


@pytest.mark.django_db
def test_provider_index_document_direct(pgv_setup):
    org = pgv_setup["org"]
    provider = PgVectorSearchProvider(dimensions=32)
    provider.index_document(
        SearchDocument(
            document_id="transcript_segment:seg-direct",
            organization_id=org.id,
            object_type="transcript_segment",
            object_id="seg-direct",
            text="contract renewal clause",
            content_hash="abc",
            metadata={"transcript_id": "t1", "text": "contract renewal clause"},
        )
    )
    row = Embedding.objects.get(
        organization=org, object_type="transcript_segment", object_id="seg-direct"
    )
    assert len(row.vector) == 32
    provider.delete_document(
        "transcript_segment:seg-direct", organization_id=org.id
    )
    assert not Embedding.objects.filter(object_id="seg-direct").exists()
