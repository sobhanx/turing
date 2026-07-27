from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import TranscriptStatus, UseCase
from turing.domain.transcript_schema import normalize_word_dict, words_to_json_list
from turing.models import Transcript, TranscriptSegment, TranscriptWord
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    NormalizedWord,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService


User = get_user_model()


def _wav_like_upload():
    # Valid extension for Phase 2.5 validation; content need not be perfect wav
    return io.BytesIO(b"RIFF____WAVEfmt "), "sample.wav"


@pytest.fixture
def transcript_with_words(db, monkeypatch):
    from turing.domain.enums import TuringRole
    from turing.models import Organization, TuringMembership

    user = User.objects.create_user(username="intel", password="pass")
    raw, name = _wav_like_upload()
    media = MediaService().create_from_upload(
        uploaded_file=raw,
        filename=name,
        content_type="audio/wav",
        use_case=UseCase.MEETING,
    )
    TuringMembership.objects.create(
        user=user,
        organization=media.organization or Organization.get_default(),
        role=TuringRole.REVIEWER,
        is_active=True,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )

    class FakeProvider:
        code = "speechmatics"

        def submit(self, request):
            return ProviderJobHandle(external_job_id="ext-w", provider_code=self.code)

        def get_status(self, handle):
            return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

        def fetch_result(self, handle):
            return NormalizedTranscript(
                language_code="en",
                full_text="S1: Hello world",
                confidence_avg=0.91,
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
                            NormalizedWord(text="Hello", start_ms=0, end_ms=400, confidence=0.95),
                            NormalizedWord(text="world", start_ms=450, end_ms=1000, confidence=0.88),
                        ],
                        raw={"provider": "fake"},
                    )
                ],
                raw={"source": "unit"},
            )

        def cancel(self, handle):
            return None

    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: FakeProvider(),
    )
    from turing.services.transcription import TranscriptionService

    transcript = TranscriptionService().process_job(str(job.id))
    return transcript, user


@pytest.mark.django_db
def test_word_and_confidence_persistence(transcript_with_words):
    transcript, _ = transcript_with_words
    assert transcript.confidence_avg == pytest.approx(0.91)
    assert transcript.word_count == 2
    segment = transcript.segments.get()
    assert segment.confidence == pytest.approx(0.9)
    assert len(segment.words) == 2
    assert segment.words[0]["text"] == "Hello"
    assert TranscriptWord.objects.filter(segment=segment).count() == 2
    assert segment.provider_payload.get("provider") == "fake"


@pytest.mark.django_db
def test_existing_transcript_without_words_still_compatible(db):
    raw, name = _wav_like_upload()
    media = MediaService().create_from_upload(
        uploaded_file=raw,
        filename=name,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=media.organization,
        status=TranscriptStatus.DRAFT,
        full_text="legacy text",
        word_count=0,
    )
    segment = TranscriptSegment.objects.create(
        transcript=transcript,
        sequence=0,
        text="legacy text",
        start_ms=0,
        end_ms=100,
        words=[],
    )
    assert segment.word_count == 2  # falls back to text split
    assert TranscriptWord.objects.filter(segment=segment).count() == 0


@pytest.mark.django_db
def test_review_status_transitions(transcript_with_words):
    transcript, user = transcript_with_words
    service = TranscriptService()
    assert transcript.status == TranscriptStatus.DRAFT

    assignment = service.submit_for_review(
        transcript=transcript,
        assignee=user,
        assigned_by=user,
    )
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatus.IN_REVIEW
    assert assignment.status == "pending"

    service.approve(transcript=transcript, approved_by=user)
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatus.APPROVED
    assert transcript.approved_by_id == user.id

    service.return_to_draft(transcript=transcript)
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatus.DRAFT


@pytest.mark.django_db
def test_transcript_search_finds_full_text_and_words(transcript_with_words):
    transcript, _ = transcript_with_words
    service = TranscriptService()
    hits = list(service.search("Hello"))
    assert transcript in hits
    hits_word = list(service.search("world"))
    assert transcript in hits_word
    assert list(service.search("zzzz-missing")) == []


def test_normalize_word_dict_provider_agnostic():
    normalized = normalize_word_dict(
        {"content": "Salam", "start_time": 1.5, "end_time": 2.0, "confidence": 0.8}
    )
    assert normalized["text"] == "Salam"
    assert normalized["start_ms"] == 1500
    assert normalized["end_ms"] == 2000
    assert normalized["confidence"] == 0.8

    payload = words_to_json_list(
        [NormalizedWord(text="Hi", start_ms=0, end_ms=10, confidence=1.0)]
    )
    assert payload[0]["text"] == "Hi"
