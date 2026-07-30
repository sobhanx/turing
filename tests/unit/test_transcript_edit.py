from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import RevisionSource, TranscriptStatus, UseCase
from turing.domain.exceptions import ValidationError
from turing.models import Organization, Speaker, Transcript, TranscriptRevision, TranscriptSegment
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService

User = get_user_model()


@pytest.fixture
def sc_user(db):
    return User.objects.create_superuser("sc-admin-edit", "sc-edit@example.com", "pass")


@pytest.fixture
def sc_media(db, sc_user):
    org = Organization.get_default()
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="edit-demo.wav",
        use_case=UseCase.GENERIC,
        organization=org,
        uploaded_by=sc_user,
    )


@pytest.fixture
def editable_transcript(db, sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=sc_media,
        organization=sc_media.organization,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="S1: Hello\nS1: World",
    )
    speaker = Speaker.objects.create(
        transcript=transcript,
        speaker_label="S1",
        speaker_name="Ada",
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=0,
        start_ms=0,
        end_ms=500,
        text="Hello",
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=1,
        start_ms=10_000,
        end_ms=11_000,
        text="World",
    )
    return transcript


@pytest.mark.django_db
def test_format_editor_body_includes_timestamp_and_speaker(editable_transcript):
    body = TranscriptService().format_editor_body(editable_transcript)
    assert "[00:00] Ada (S1)" in body
    assert "[00:10] Ada (S1)" in body
    assert "Hello" in body
    assert "World" in body
    assert "\n\n" in body


@pytest.mark.django_db
def test_update_editor_body_round_trip(editable_transcript, sc_user):
    service = TranscriptService()
    original = service.format_editor_body(editable_transcript)
    updated_body = original.replace("Hello", "Hi there").replace("World", "Goodbye")
    updated = service.update_editor_body(
        editable_transcript,
        updated_body,
        edited_by=sc_user,
    )
    segments = list(updated.segments.order_by("sequence"))
    assert segments[0].text == "Hi there"
    assert segments[1].text == "Goodbye"
    assert "Hi there" in updated.full_text
    revision = TranscriptRevision.objects.filter(transcript=updated).latest(
        "revision_number"
    )
    assert revision.source == RevisionSource.HUMAN
    assert revision.change_summary == "Transcript edited"


@pytest.mark.django_db
def test_update_editor_body_rejects_block_count_mismatch(editable_transcript, sc_user):
    with pytest.raises(ValidationError, match="Expected 2 segment blocks"):
        TranscriptService().update_editor_body(
            editable_transcript,
            "[00:00] S1\nOnly one block",
            edited_by=sc_user,
        )


@pytest.mark.django_db
def test_update_editor_body_full_text_only(db, sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=sc_media,
        organization=sc_media.organization,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="Plain transcript text",
    )
    updated = TranscriptService().update_editor_body(
        transcript,
        "Updated plain text",
        edited_by=sc_user,
    )
    assert updated.full_text == "Updated plain text"
    assert TranscriptRevision.objects.filter(transcript=updated).exists()
