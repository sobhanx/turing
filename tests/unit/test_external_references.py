from __future__ import annotations

import io

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from turing.domain.enums import ExternalReferenceTarget, UseCase
from turing.models import ExternalReference, Organization
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcription import TranscriptionService
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
)


class _FakeSTTProvider:
    code = "speechmatics"

    def submit(self, request):
        return ProviderJobHandle(external_job_id="ext-ref-1", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(external_job_id=handle.external_job_id, state="succeeded")

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Hello.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Hello.",
                    start_ms=0,
                    end_ms=500,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )


@pytest.fixture
def media(db):
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call.wav",
        use_case=UseCase.CRM_CALL,
    )


@pytest.fixture
def transcript(db, media, monkeypatch):
    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _FakeSTTProvider(),
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        language_code="en",
        auto_enqueue=False,
    )
    service = TranscriptionService()
    service.submit(str(job.id))
    return service.fetch_and_persist(str(job.id))


@pytest.mark.django_db
def test_link_media_to_host_object(media):
    ref = ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )

    assert ref.target_kind == ExternalReferenceTarget.MEDIA
    assert ref.target == media
    assert ref.transcript_id is None
    assert media.external_references.filter(pk=ref.pk).exists()


@pytest.mark.django_db
def test_link_transcript_to_host_object(transcript):
    ref = ExternalReference.objects.create(
        organization=transcript.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        transcript=transcript,
    )

    assert ref.target_kind == ExternalReferenceTarget.TRANSCRIPT
    assert ref.target == transcript
    assert ref.media_id is None
    assert transcript.external_references.filter(pk=ref.pk).exists()


@pytest.mark.django_db
def test_host_lookup_by_external_key(media):
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    other = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"other"),
        filename="other.wav",
        use_case=UseCase.CRM_CALL,
        organization_id=media.organization_id,
    )
    ExternalReference.objects.create(
        organization=other.organization,
        external_system="crm",
        external_type="deal",
        external_id="99999",
        media=other,
    )

    matches = ExternalReference.objects.filter(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
    )
    assert matches.count() == 1
    assert matches.get().media_id == media.id


@pytest.mark.django_db
def test_same_host_object_can_link_multiple_targets(media, transcript):
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    ExternalReference.objects.create(
        organization=transcript.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        transcript=transcript,
    )
    assert (
        ExternalReference.objects.filter(
            external_system="crm",
            external_type="deal",
            external_id="12345",
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_same_media_can_have_multiple_host_links(media):
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="contact",
        external_id="c-99",
        media=media,
    )
    assert media.external_references.count() == 2


@pytest.mark.django_db
def test_duplicate_media_host_link_rejected(media):
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ExternalReference.objects.create(
                organization=media.organization,
                external_system="crm",
                external_type="deal",
                external_id="12345",
                media=media,
            )


@pytest.mark.django_db
def test_neither_target_rejected_by_clean(media):
    ref = ExternalReference(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
    )
    with pytest.raises(ValidationError):
        ref.full_clean()


@pytest.mark.django_db
def test_both_targets_rejected_by_clean(media, transcript):
    ref = ExternalReference(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
        transcript=transcript,
    )
    with pytest.raises(ValidationError):
        ref.full_clean()


@pytest.mark.django_db
def test_both_targets_rejected_by_db_constraint(media, transcript):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ExternalReference.objects.create(
                organization=media.organization,
                external_system="crm",
                external_type="deal",
                external_id="both",
                media=media,
                transcript=transcript,
            )


@pytest.mark.django_db
def test_organization_must_match_media(media):
    other_org = Organization.objects.create(name="Other", slug="other-extref")
    ref = ExternalReference(
        organization=other_org,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    with pytest.raises(ValidationError):
        ref.full_clean()


@pytest.mark.django_db
def test_deleting_media_cascades_references(media):
    ExternalReference.objects.create(
        organization=media.organization,
        external_system="crm",
        external_type="deal",
        external_id="12345",
        media=media,
    )
    media_id = media.id
    media.delete()
    assert not ExternalReference.objects.filter(media_id=media_id).exists()
