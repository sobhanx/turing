from __future__ import annotations

"""Meeting / Recording abstraction — vendor-independent layer over MediaAsset."""

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from turing.connectors.base import MediaPullItem
from turing.connectors.media_ingest import sync_media_pull_items
from turing.domain.enums import (
    ConnectorInstallationStatus,
    MeetingStatus,
    RecordingStatus,
    TuringRole,
    UseCase,
)
from turing.domain.exceptions import PermissionDeniedError, ValidationError
from turing.domain.meeting_schema import NormalizedMeeting, NormalizedRecording
from turing.models import (
    ConnectorInstallation,
    Meeting,
    MediaAsset,
    Organization,
    Recording,
    TuringMembership,
)
from turing.services.media import MediaService
from turing.services.meeting import MeetingService, meeting_external_id_from_item

User = get_user_model()


@pytest.fixture
def org(db):
    return Organization.get_default()


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Other Meet Org", slug="other-meet-org")


@pytest.fixture
def editor(db, org):
    user = User.objects.create_user("meet-editor", password="pass")
    TuringMembership.objects.create(
        user=user, organization=org, role=TuringRole.EDITOR, is_active=True
    )
    return user


def _wav() -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


@pytest.mark.django_db
def test_create_meeting(org, editor):
    meeting = MeetingService().upsert_meeting(
        organization=org,
        provider="zoom",
        external_id="m-100",
        title="Standup",
        status=MeetingStatus.ENDED,
        user=editor,
    )
    assert meeting.organization_id == org.id
    assert meeting.provider == "zoom"
    assert meeting.external_id == "m-100"
    assert meeting.title == "Standup"
    assert Meeting.objects.filter(pk=meeting.pk).exists()


@pytest.mark.django_db
def test_attach_recording_and_link_media(org, editor):
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav()),
        filename="meet.wav",
        content_type="audio/wav",
        use_case=UseCase.MEETING,
        organization=org,
        uploaded_by=editor,
    )
    meeting = MeetingService().upsert_meeting(
        organization=org,
        provider="zoom",
        external_id="m-200",
        title="Retro",
        user=editor,
    )
    recording = MeetingService().attach_recording(
        meeting=meeting,
        external_id="rec-200",
        source_url="https://example.test/r.wav",
        media=media,
        user=editor,
    )
    assert recording.meeting_id == meeting.id
    assert recording.media_id == media.id
    assert recording.status == RecordingStatus.INGESTED
    assert media.meeting_recording.id == recording.id


@pytest.mark.django_db
def test_duplicate_external_recording_reuses_row(org):
    svc = MeetingService()
    meeting = svc.upsert_meeting(
        organization=org, provider="teams", external_id="tm-1", title="A"
    )
    media1 = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav()),
        filename="a.wav",
        content_type="audio/wav",
        organization=org,
    )
    r1 = svc.attach_recording(
        meeting=meeting, external_id="rec-dup", media=media1
    )
    media2 = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav()),
        filename="b.wav",
        content_type="audio/wav",
        organization=org,
    )
    r2 = svc.attach_recording(
        meeting=meeting, external_id="rec-dup", media=media2
    )
    assert r1.id == r2.id
    assert Recording.objects.filter(provider="teams", external_id="rec-dup").count() == 1
    assert r2.media_id == media2.id


@pytest.mark.django_db
def test_meeting_organization_isolation(org, other_org, editor):
    svc = MeetingService()
    svc.upsert_meeting(
        organization=org, provider="zoom", external_id="same-key", title="Mine"
    )
    svc.upsert_meeting(
        organization=other_org, provider="zoom", external_id="same-key", title="Theirs"
    )
    assert Meeting.objects.filter(provider="zoom", external_id="same-key").count() == 2
    assert (
        Meeting.objects.filter(organization=org, external_id="same-key").get().title
        == "Mine"
    )

    with pytest.raises(PermissionDeniedError):
        svc.upsert_meeting(
            organization=other_org,
            provider="zoom",
            external_id="blocked",
            user=editor,
        )


@pytest.mark.django_db
def test_sync_media_pull_items_creates_meeting_and_recording(org, monkeypatch):
    installation = ConnectorInstallation.objects.create(
        organization=org,
        connector_type="zoom",
        name="Zoom Test",
        status=ConnectorInstallationStatus.ACTIVE,
        config={},
    )

    created_assets: list[MediaAsset] = []

    def fake_create(**kwargs):
        asset = MediaService().create_from_upload(
            uploaded_file=io.BytesIO(_wav()),
            filename=kwargs.get("original_filename") or "z.wav",
            content_type="audio/wav",
            use_case=UseCase.MEETING,
            organization=kwargs["organization"],
            metadata=kwargs.get("metadata"),
        )
        created_assets.append(asset)
        return asset, "downloaded"

    monkeypatch.setattr(
        "turing.connectors.media_ingest.create_media_from_connector_url",
        fake_create,
    )

    items = [
        MediaPullItem(
            external_id="zoom-rec-1",
            source_url="https://example.test/rec.wav",
            filename="rec.wav",
            metadata={
                "meeting_id": "zoom-meet-9",
                "topic": "All Hands",
                "recording_start": "2026-07-01T10:00:00Z",
                "recording_end": "2026-07-01T11:00:00Z",
            },
            meeting_external_id="zoom-meet-9",
        )
    ]
    result = sync_media_pull_items(
        installation=installation,
        items=items,
        external_system="zoom",
        external_type="meeting",
        use_case=UseCase.MEETING,
        metadata_namespace="zoom",
    )
    assert result.records_processed == 1
    meeting = Meeting.objects.get(
        organization=org, provider="zoom", external_id="zoom-meet-9"
    )
    assert meeting.title == "All Hands"
    recording = Recording.objects.get(
        organization=org, provider="zoom", external_id="zoom-rec-1"
    )
    assert recording.meeting_id == meeting.id
    assert recording.media_id == created_assets[0].id
    assert recording.status == RecordingStatus.INGESTED

    # Duplicate sync skips media create and does not duplicate recording.
    result2 = sync_media_pull_items(
        installation=installation,
        items=items,
        external_system="zoom",
        external_type="meeting",
        use_case=UseCase.MEETING,
        metadata_namespace="zoom",
    )
    assert result2.details["skipped"] == 1
    assert Recording.objects.filter(external_id="zoom-rec-1").count() == 1


@pytest.mark.django_db
def test_upload_flow_unaffected_by_meeting_layer(client, db):
    user = User.objects.create_superuser("meet-up", "u@example.com", "pass")
    client.force_login(user)
    org = Organization.get_default()
    before_meetings = Meeting.objects.count()
    before_recordings = Recording.objects.count()
    resp = client.post(
        reverse("speech_center:upload_media"),
        {
            "organization_id": str(org.id),
            "file": SimpleUploadedFile("plain.wav", _wav(), content_type="audio/wav"),
        },
    )
    assert resp.status_code == 302
    assert MediaAsset.objects.filter(original_filename="plain.wav").exists()
    assert Meeting.objects.count() == before_meetings
    assert Recording.objects.count() == before_recordings


def test_normalized_dtos_exist():
    m = NormalizedMeeting(external_id="1", provider="zoom", title="T")
    r = NormalizedRecording(
        external_id="r1", provider="zoom", meeting_external_id="1"
    )
    assert m.external_id == "1"
    assert r.meeting_external_id == "1"


def test_meeting_external_id_resolution():
    item = MediaPullItem(
        external_id="rec-only",
        metadata={"meeting_id": "m-1"},
    )
    assert meeting_external_id_from_item(item) == "m-1"
    item2 = MediaPullItem(external_id="rec-2", meeting_external_id="m-2")
    assert meeting_external_id_from_item(item2) == "m-2"
    item3 = MediaPullItem(external_id="rec-3")
    assert meeting_external_id_from_item(item3) == "rec-3"


@pytest.mark.django_db
def test_link_media_rejects_cross_org(org, other_org):
    svc = MeetingService()
    meeting = svc.upsert_meeting(
        organization=org, provider="zoom", external_id="x", title="X"
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav()),
        filename="other.wav",
        content_type="audio/wav",
        organization=other_org,
    )
    recording = svc.attach_recording(meeting=meeting, external_id="rx")
    with pytest.raises(ValidationError):
        svc.link_media(recording, media)
