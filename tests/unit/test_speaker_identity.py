from __future__ import annotations

"""Speaker identity: speaker_label immutable, speaker_name editable + propagation."""

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
    NormalizedWord,
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
from turing.services.transcript import TranscriptService
from turing.services.transcription import TranscriptionService

User = get_user_model()


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-spk", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Hello world",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Hello world",
                    start_ms=0,
                    end_ms=1000,
                    confidence=0.9,
                    speaker_label="S1",
                    words=[
                        NormalizedWord(
                            text="Hello",
                            start_ms=0,
                            end_ms=400,
                            confidence=0.9,
                            speaker_label="S1",
                        ),
                        NormalizedWord(
                            text="world",
                            start_ms=400,
                            end_ms=1000,
                            confidence=0.9,
                            speaker_label="S1",
                        ),
                    ],
                ),
            ],
        )


@pytest.fixture
def speaker_setup(db, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()

    org = Organization.get_default()
    user = User.objects.create_user(username="spk-editor", password="pass")
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.EDITOR, is_active=True
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="spk.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    transcript = service.fetch_and_persist(str(job.id))
    speaker = transcript.speakers.get(speaker_label="S1")
    return {
        "org": org,
        "user": user,
        "transcript": transcript,
        "speaker": speaker,
        "media": media,
    }


@pytest.mark.django_db
def test_persist_stamps_speaker_label_without_name(speaker_setup):
    speaker = speaker_setup["speaker"]
    assert speaker.speaker_label == "S1"
    assert speaker.speaker_name == ""
    assert speaker.resolved_name == "S1"

    segment = speaker_setup["transcript"].segments.get()
    assert segment.words[0]["speaker_label"] == "S1"
    assert "speaker_name" not in segment.words[0] or not segment.words[0].get(
        "speaker_name"
    )
    word = segment.word_entries.first()
    assert word.metadata.get("speaker_label") == "S1"


@pytest.mark.django_db
def test_rename_propagates_to_segments_words_search_and_api(speaker_setup):
    transcript = speaker_setup["transcript"]
    speaker = speaker_setup["speaker"]
    emb = LocalNeuralEmbeddingProvider(model_name="turing-local-small", dimensions=64)
    search = PgVectorSearchProvider(embedding_provider=emb)
    SearchIndexService(provider=search, embedding_provider=emb).index_transcript(
        transcript
    )

    TranscriptService().rename_speaker(
        speaker=speaker,
        speaker_name="Ada Lovelace",
        edited_by=speaker_setup["user"],
    )
    speaker.refresh_from_db()
    assert speaker.speaker_label == "S1"  # immutable
    assert speaker.speaker_name == "Ada Lovelace"
    assert speaker.resolved_name == "Ada Lovelace"

    segment = transcript.segments.get()
    segment.refresh_from_db()
    assert all(w.get("speaker_label") == "S1" for w in segment.words)
    assert all(w.get("speaker_name") == "Ada Lovelace" for w in segment.words)

    for tw in segment.word_entries.all():
        assert tw.metadata.get("speaker_label") == "S1"
        assert tw.metadata.get("speaker_name") == "Ada Lovelace"

    transcript.refresh_from_db()
    assert "Ada Lovelace:" in transcript.full_text

    emb_row = Embedding.objects.filter(
        organization=speaker_setup["org"],
        metadata__segment_id=str(segment.id),
    ).first()
    assert emb_row is not None
    assert emb_row.metadata.get("speaker") == "Ada Lovelace"
    assert emb_row.metadata.get("speaker_label") == "S1"
    assert emb_row.metadata.get("speaker_name") == "Ada Lovelace"

    # API surfaces
    client = APIClient()
    client.force_authenticate(user=speaker_setup["user"])
    sp = client.get(f"/api/turing/v1/speakers/{speaker.id}/")
    assert sp.status_code == 200
    assert sp.data["speaker_label"] == "S1"
    assert sp.data["speaker_name"] == "Ada Lovelace"
    assert sp.data["resolved_name"] == "Ada Lovelace"

    seg = client.get(f"/api/turing/v1/segments/{segment.id}/")
    assert seg.status_code == 200
    assert seg.data["speaker_label"] == "S1"
    assert seg.data["speaker_name"] == "Ada Lovelace"
    assert seg.data["words"][0]["speaker_name"] == "Ada Lovelace"

    rag = RAGService(search_provider=search).retrieve_context(
        "Hello", speaker_setup["org"]
    )
    assert rag
    assert rag[0]["speaker"] == "Ada Lovelace"
    assert rag[0]["speaker_label"] == "S1"


@pytest.mark.django_db
def test_rename_via_api_accepts_speaker_name_and_display_name_alias(speaker_setup):
    client = APIClient()
    client.force_authenticate(user=speaker_setup["user"])
    speaker = speaker_setup["speaker"]

    resp = client.patch(
        f"/api/turing/v1/speakers/{speaker.id}/",
        {"speaker_name": "Agent"},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["speaker_name"] == "Agent"
    assert resp.data["speaker_label"] == "S1"

    resp2 = client.patch(
        f"/api/turing/v1/speakers/{speaker.id}/",
        {"display_name": "Rep"},
        format="json",
    )
    assert resp2.status_code == 200
    assert resp2.data["speaker_name"] == "Rep"
    speaker.refresh_from_db()
    assert speaker.speaker_label == "S1"
