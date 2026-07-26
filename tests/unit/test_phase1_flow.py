from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import JobStatus, TranscriptStatus, UseCase
from turing.providers.speechmatics.mapper import map_speechmatics_transcript
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService
from turing.services.transcription import TranscriptionService


User = get_user_model()


def test_speechmatics_mapper_builds_segments_and_speakers():
    payload = {
        "metadata": {"language_pack_info": {"language_code": "en"}},
        "results": [
            {
                "type": "word",
                "start_time": 0.0,
                "end_time": 0.4,
                "alternatives": [
                    {"content": "Hello", "confidence": 0.9, "speaker": "S1"}
                ],
            },
            {
                "type": "word",
                "start_time": 0.4,
                "end_time": 0.8,
                "alternatives": [
                    {"content": "world", "confidence": 0.8, "speaker": "S1"}
                ],
            },
            {
                "type": "punctuation",
                "is_eos": True,
                "alternatives": [{"content": "."}],
            },
            {
                "type": "word",
                "start_time": 1.0,
                "end_time": 1.3,
                "alternatives": [
                    {"content": "Hi", "confidence": 0.95, "speaker": "S2"}
                ],
            },
            {
                "type": "punctuation",
                "is_eos": True,
                "alternatives": [{"content": "."}],
            },
        ],
    }
    result = map_speechmatics_transcript(payload)
    assert result.language_code == "en"
    assert len(result.segments) == 2
    assert result.segments[0].speaker_label == "S1"
    assert "Hello" in result.segments[0].text
    assert {s.label for s in result.speakers} == {"S1", "S2"}


@pytest.mark.django_db
def test_media_upload_and_job_creation(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    user = User.objects.create_user(username="alice", password="pass")
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"fake-audio-bytes"),
        filename="call.wav",
        content_type="audio/wav",
        use_case=UseCase.CRM_CALL,
        uploaded_by=user,
    )
    assert media.byte_size > 0
    assert media.checksum

    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=user,
        auto_enqueue=False,
        language_code="en",
        options={"diarization": True},
    )
    assert job.status == JobStatus.PENDING
    assert job.provider_code == "speechmatics"
    assert job.media_id == media.id


@pytest.mark.django_db
def test_transcription_persist_and_human_edit(monkeypatch):
    user = User.objects.create_user(username="editor", password="pass")
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"abc"),
        filename="meeting.wav",
        use_case=UseCase.MEETING,
        uploaded_by=user,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=user,
        auto_enqueue=False,
    )

    class FakeProvider:
        code = "speechmatics"

        def submit(self, request):
            return ProviderJobHandle(external_job_id="ext-1", provider_code=self.code)

        def get_status(self, handle):
            return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

        def fetch_result(self, handle):
            return NormalizedTranscript(
                language_code="en",
                full_text="S1: Hello there",
                confidence_avg=0.9,
                speakers=[NormalizedSpeaker(label="S1", display_name="S1")],
                segments=[
                    NormalizedSegment(
                        sequence=0,
                        text="Hello there",
                        start_ms=0,
                        end_ms=1200,
                        confidence=0.9,
                        speaker_label="S1",
                    )
                ],
            )

        def cancel(self, handle):
            return None

    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: FakeProvider(),
    )

    transcript = TranscriptionService().process_job(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert transcript.status == TranscriptStatus.DRAFT
    assert transcript.segments.count() == 1
    assert transcript.revisions.count() == 1

    segment = transcript.segments.get()
    TranscriptService().update_segment(
        segment=segment,
        text="Hello everyone",
        edited_by=user,
    )
    transcript.refresh_from_db()
    assert transcript.version == 2
    assert transcript.revisions.count() == 2
    assert "Hello everyone" in transcript.full_text
