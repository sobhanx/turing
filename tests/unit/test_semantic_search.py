from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.domain.enums import TuringRole, UseCase
from turing.domain.events import analysis_completed, transcript_created
from turing.events.bus import EventBus
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
    SemanticSearchProviderNotFoundError,
    SemanticSearchRegistry,
    register_builtin_search_providers,
)
from turing.search.embeddings import register_builtin_embedding_providers
from turing.search.embeddings.registry import EmbeddingProviderRegistry
from turing.search.handlers import (
    on_analysis_completed,
    on_transcript_created,
    register_search_handlers,
)
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
        return ProviderJobHandle(external_job_id="ext-search", provider_code=self.code)

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


def _membership(user, org, role: str) -> TuringMembership:
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


@pytest.fixture(autouse=True)
def _search_registry():
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()
    EventBus.clear()
    register_search_handlers()
    yield
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()
    EventBus.clear()
    register_search_handlers()


@pytest.fixture
def search_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    org = Organization.get_default()
    viewer = User.objects.create_user(username="search-viewer", password="pass")
    outsider = User.objects.create_user(username="search-outsider", password="pass")
    other_org = Organization.objects.create(name="Other Search", slug="search-other")
    _membership(viewer, org, TuringRole.VIEWER)
    _membership(outsider, other_org, TuringRole.VIEWER)

    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="search-call.wav",
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
        external_id="SF-SEARCH-1",
    )
    return {
        "org": org,
        "other_org": other_org,
        "viewer": viewer,
        "outsider": outsider,
        "transcript": transcript,
        "media": media,
    }


def test_provider_registry():
    assert "null" in SemanticSearchRegistry.codes()
    assert "pgvector" in SemanticSearchRegistry.codes()
    null = SemanticSearchRegistry.create("null")
    assert isinstance(null, NullSemanticSearchProvider)
    assert null.search("q", organization_id=1).hits == []
    with pytest.raises(SemanticSearchProviderNotFoundError):
        SemanticSearchRegistry.get("does-not-exist")


@pytest.mark.django_db
def test_indexing_service(search_setup):
    from turing.search.embeddings import NullEmbeddingProvider

    transcript = search_setup["transcript"]
    # Null search + null embedding → Embedding rows with empty vectors.
    count = SearchIndexService(
        provider=NullSemanticSearchProvider(),
        embedding_provider=NullEmbeddingProvider(),
    ).index_transcript(transcript)
    assert count == 2
    rows = Embedding.objects.filter(organization=search_setup["org"])
    assert rows.count() == 2
    row = rows.first()
    assert row.object_type == "transcript_segment"
    assert row.metadata["transcript_id"] == str(transcript.id)
    assert row.metadata["media_id"] == str(search_setup["media"].id)
    assert "start_ms" in row.metadata
    assert row.metadata["external_references"]
    assert row.vector == []
    assert row.provider == "null"

    removed = SearchIndexService(
        provider=NullSemanticSearchProvider(),
        embedding_provider=NullEmbeddingProvider(),
    ).remove_index(transcript)
    assert removed == 2
    assert Embedding.objects.filter(organization=search_setup["org"]).count() == 0


@pytest.mark.django_db
def test_event_handler_isolation(search_setup, monkeypatch):
    transcript = search_setup["transcript"]

    def _boom(*args, **kwargs):
        raise RuntimeError("index down")

    monkeypatch.setattr(
        "turing.services.search_index.SearchIndexService.index_transcript",
        _boom,
    )
    # Must not raise into caller / pipeline.
    on_transcript_created(
        transcript_created(
            transcript_id=str(transcript.id),
            organization_id=search_setup["org"].id,
            media_id=str(search_setup["media"].id),
        )
    )
    on_analysis_completed(
        analysis_completed(
            transcript_id=str(transcript.id),
            organization_id=search_setup["org"].id,
            analysis_ids=["a1"],
            analysis_types=["summary"],
        )
    )


@pytest.mark.django_db(transaction=True)
def test_event_handler_indexes(search_setup):
    transcript = search_setup["transcript"]
    EventBus.emit(
        transcript_created(
            transcript_id=str(transcript.id),
            organization_id=search_setup["org"].id,
            media_id=str(search_setup["media"].id),
        )
    )
    assert Embedding.objects.filter(organization=search_setup["org"]).count() == 2


@pytest.mark.django_db
def test_org_isolation_with_null_provider(search_setup, settings):
    settings.TURING_SEARCH_PROVIDER = "null"
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()

    transcript = search_setup["transcript"]
    SearchIndexService(provider=NullSemanticSearchProvider()).index_transcript(
        transcript
    )

    viewer = APIClient()
    viewer.force_authenticate(user=search_setup["viewer"])
    ok = viewer.get(SEARCH_URL, {"q": "renewal"})
    assert ok.status_code == 200
    assert ok.data["results"] == []
    assert ok.data["provider"] == "null"

    outsider = APIClient()
    outsider.force_authenticate(user=search_setup["outsider"])
    other = outsider.get(SEARCH_URL, {"q": "renewal"})
    assert other.status_code == 200
    assert other.data["results"] == []
    assert other.data["provider"] == "null"


@pytest.mark.django_db
def test_search_requires_auth():
    anon = APIClient()
    assert anon.get(SEARCH_URL).status_code in {401, 403}
