"""Phase 2.7 — Authorization & Tenancy tests."""

from __future__ import annotations

import io
import wave

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from turing.auth.roles import get_user_role, user_has_capability
from turing.auth.tenancy import resolve_organization, scope_by_organization
from turing.domain.enums import TuringRole
from turing.models import (
    MediaAsset,
    Organization,
    ProcessingJob,
    Transcript,
    TuringMembership,
)
from turing.providers.types import (
    NormalizedSegment,
    NormalizedTranscript,
    NormalizedWord,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService

User = get_user_model()


def _wav_bytes(duration_sec: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * duration_sec))
    return buf.getvalue()


@pytest.fixture
def orgs(db):
    default = Organization.get_default()
    alpha = Organization.objects.create(name="Alpha", slug="alpha", is_active=True)
    beta = Organization.objects.create(name="Beta", slug="beta", is_active=True)
    return {"default": default, "alpha": alpha, "beta": beta}


def _membership(user, org, role: str) -> TuringMembership:
    return TuringMembership.objects.create(
        user=user, organization=org, role=role, is_active=True
    )


def _media_in_org(org, *, username: str = "uploader", role: str = TuringRole.USER) -> MediaAsset:
    user = User.objects.create_user(username=username, password="pass")
    _membership(user, org, role)
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename=f"{username}.wav",
        content_type="audio/wav",
        uploaded_by=user,
        organization=org,
    )


@pytest.mark.django_db
def test_editor_lacks_approve_capability(orgs):
    editor = User.objects.create_user(username="ed", password="pass")
    _membership(editor, orgs["alpha"], TuringRole.EDITOR)
    assert user_has_capability(editor, "edit_transcript", organization=orgs["alpha"])
    assert not user_has_capability(
        editor, "approve_transcript", organization=orgs["alpha"]
    )
    assert not user_has_capability(editor, "approve_transcript")


@pytest.mark.django_db
def test_reviewer_can_approve_in_own_org_only(orgs):
    reviewer = User.objects.create_user(username="rev", password="pass")
    _membership(reviewer, orgs["alpha"], TuringRole.REVIEWER)
    assert user_has_capability(
        reviewer, "approve_transcript", organization=orgs["alpha"]
    )
    assert not user_has_capability(
        reviewer, "approve_transcript", organization=orgs["beta"]
    )


@pytest.mark.django_db
def test_queryset_scoped_by_organization(orgs):
    media_a = _media_in_org(orgs["alpha"], username="a_user")
    media_b = _media_in_org(orgs["beta"], username="b_user")
    viewer = User.objects.create_user(username="viewer", password="pass")
    _membership(viewer, orgs["alpha"], TuringRole.VIEWER)

    scoped = scope_by_organization(MediaAsset.objects.all(), viewer)
    assert set(scoped.values_list("id", flat=True)) == {media_a.id}
    assert media_b.id not in set(scoped.values_list("id", flat=True))


@pytest.mark.django_db
def test_staff_without_membership_sees_no_organizations(orgs):
    """is_staff alone does not grant cross-org visibility."""
    media_a = _media_in_org(orgs["alpha"], username="sa")
    media_b = _media_in_org(orgs["beta"], username="sb")
    staff = User.objects.create_user(username="staff", password="pass", is_staff=True)

    scoped = scope_by_organization(MediaAsset.objects.all(), staff)
    assert scoped.count() == 0
    assert media_a.id not in set(scoped.values_list("id", flat=True))
    assert media_b.id not in set(scoped.values_list("id", flat=True))


@pytest.mark.django_db
def test_no_membership_has_no_capabilities(orgs):
    user = User.objects.create_user(username="ghost", password="pass")
    assert get_user_role(user) is None
    assert get_user_role(user, organization=orgs["alpha"]) is None
    assert not user_has_capability(user, "view_transcript")
    assert not user_has_capability(
        user, "view_transcript", organization=orgs["alpha"]
    )
    assert not user_has_capability(user, "upload_media")
    assert not user_has_capability(user, "manage_jobs")
    assert not user_has_capability(user, "approve_transcript")


@pytest.mark.django_db
def test_staff_without_membership_has_no_turing_permissions(orgs):
    staff = User.objects.create_user(
        username="staff_only", password="pass", is_staff=True
    )
    assert get_user_role(staff) is None
    assert not user_has_capability(staff, "view_transcript")
    assert not user_has_capability(
        staff, "approve_transcript", organization=orgs["alpha"]
    )
    assert not user_has_capability(staff, "upload_media")
    assert not user_has_capability(staff, "manage_jobs")


@pytest.mark.django_db
def test_superuser_has_full_access(orgs):
    media_a = _media_in_org(orgs["alpha"], username="su_a")
    media_b = _media_in_org(orgs["beta"], username="su_b")
    su = User.objects.create_superuser("root", "root@example.com", "pass")

    assert user_has_capability(su, "approve_transcript")
    assert user_has_capability(
        su, "approve_transcript", organization=orgs["beta"]
    )
    assert user_has_capability(su, "upload_media", organization=orgs["alpha"])
    scoped = scope_by_organization(MediaAsset.objects.all(), su)
    ids = set(scoped.values_list("id", flat=True))
    assert media_a.id in ids and media_b.id in ids

    # Explicit foreign org create allowed for superuser
    org = resolve_organization(organization_id=orgs["beta"].id, user=su)
    assert org.id == orgs["beta"].id


@pytest.mark.django_db
def test_membership_org_a_cannot_operate_in_org_b(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    user = User.objects.create_user(username="a_only", password="pass")
    _membership(user, orgs["alpha"], TuringRole.ADMIN)
    assert user_has_capability(user, "manage_jobs", organization=orgs["alpha"])
    assert not user_has_capability(user, "manage_jobs", organization=orgs["beta"])
    with pytest.raises(PermissionDeniedError):
        resolve_organization(
            organization=orgs["beta"],
            user=user,
            capability="manage_jobs",
        )


@pytest.mark.django_db
def test_multiple_memberships_evaluate_per_organization(orgs):
    user = User.objects.create_user(username="multi_cap", password="pass")
    _membership(user, orgs["alpha"], TuringRole.VIEWER)
    _membership(user, orgs["beta"], TuringRole.REVIEWER)
    assert not user_has_capability(
        user, "approve_transcript", organization=orgs["alpha"]
    )
    assert user_has_capability(
        user, "approve_transcript", organization=orgs["beta"]
    )
    assert user_has_capability(user, "view_transcript", organization=orgs["alpha"])
    # Unscoped: any membership — Reviewer in B grants approve
    assert user_has_capability(user, "approve_transcript")


@pytest.mark.django_db
def test_job_and_transcript_inherit_organization(orgs):
    media = _media_in_org(orgs["alpha"], username="inherit")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    assert job.organization_id == orgs["alpha"].id

    normalized = NormalizedTranscript(
        full_text="Hello",
        language_code="en",
        confidence_avg=0.9,
        segments=[
            NormalizedSegment(
                sequence=0,
                start_ms=0,
                end_ms=500,
                text="Hello",
                confidence=0.9,
                words=[NormalizedWord(text="Hello", start_ms=0, end_ms=500, confidence=0.9)],
            )
        ],
        speakers=[],
        raw={},
    )
    transcript = TranscriptService().persist_from_provider(job=job, normalized=normalized)
    assert transcript.organization_id == orgs["alpha"].id


@pytest.mark.django_db
def test_resolve_organization_falls_back_to_default(orgs):
    org = resolve_organization()
    assert org.slug == "default"


@pytest.mark.django_db
def test_resolve_explicit_foreign_org_denied(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    user = User.objects.create_user(username="alpha_only", password="pass")
    _membership(user, orgs["alpha"], TuringRole.USER)
    with pytest.raises(PermissionDeniedError):
        resolve_organization(organization_id=orgs["beta"].id, user=user)
    with pytest.raises(PermissionDeniedError):
        resolve_organization(tenant_key="beta", user=user)
    # Explicit target must not fall back to Default
    with pytest.raises(PermissionDeniedError):
        resolve_organization(organization=orgs["beta"], user=user)


@pytest.mark.django_db
def test_resolve_implicit_requires_membership(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    user = User.objects.create_user(username="orphan", password="pass")
    with pytest.raises(PermissionDeniedError, match="belong to an organization"):
        resolve_organization(user=user)


@pytest.mark.django_db
def test_user_cannot_upload_to_foreign_organization(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    user = User.objects.create_user(username="uploader_a", password="pass")
    _membership(user, orgs["alpha"], TuringRole.USER)
    with pytest.raises(PermissionDeniedError):
        MediaService().create_from_upload(
            uploaded_file=io.BytesIO(_wav_bytes()),
            filename="cross.wav",
            content_type="audio/wav",
            uploaded_by=user,
            organization_id=orgs["beta"].id,
        )


@pytest.mark.django_db
def test_api_upload_to_foreign_org_denied(orgs):
    from django.core.files.uploadedfile import SimpleUploadedFile

    user = User.objects.create_user(username="api_cross2", password="pass")
    _membership(user, orgs["alpha"], TuringRole.USER)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        "/api/turing/v1/media/",
        {
            "file": SimpleUploadedFile("x.wav", _wav_bytes(), content_type="audio/wav"),
            "organization_id": orgs["beta"].id,
        },
        format="multipart",
    )
    assert response.status_code == 403
    assert response.data["code"] == "permission_denied"


@pytest.mark.django_db
def test_api_same_org_upload_succeeds(orgs):
    from django.core.files.uploadedfile import SimpleUploadedFile

    user = User.objects.create_user(username="api_same", password="pass")
    _membership(user, orgs["alpha"], TuringRole.USER)
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        "/api/turing/v1/media/",
        {
            "file": SimpleUploadedFile("ok.wav", _wav_bytes(), content_type="audio/wav"),
            "organization_id": orgs["alpha"].id,
        },
        format="multipart",
    )
    assert response.status_code == 201
    assert response.data["organization"] == orgs["alpha"].id


@pytest.mark.django_db
def test_user_without_membership_cannot_create_media(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    user = User.objects.create_user(username="no_member", password="pass")
    with pytest.raises(PermissionDeniedError):
        MediaService().create_from_upload(
            uploaded_file=io.BytesIO(_wav_bytes()),
            filename="nope.wav",
            content_type="audio/wav",
            uploaded_by=user,
        )


@pytest.mark.django_db
def test_job_create_for_foreign_media_denied_at_service(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    media_b = _media_in_org(orgs["beta"], username="beta_media")
    user_a = User.objects.create_user(username="alpha_job", password="pass")
    _membership(user_a, orgs["alpha"], TuringRole.ADMIN)
    with pytest.raises(PermissionDeniedError):
        JobOrchestrator().create_transcription_job(
            media=media_b,
            language_code="en",
            created_by=user_a,
            auto_enqueue=False,
        )


@pytest.mark.django_db
def test_capability_is_org_scoped_not_global_max(orgs):
    """Viewer in A + User in B must not get upload_media for A via max-role."""
    from turing.domain.exceptions import PermissionDeniedError

    user = User.objects.create_user(username="split_roles", password="pass")
    _membership(user, orgs["alpha"], TuringRole.VIEWER)
    _membership(user, orgs["beta"], TuringRole.USER)
    assert user_has_capability(user, "upload_media", organization=orgs["beta"])
    assert not user_has_capability(user, "upload_media", organization=orgs["alpha"])
    # Unscoped: true because B grants it
    assert user_has_capability(user, "upload_media")
    with pytest.raises(PermissionDeniedError):
        MediaService().create_from_upload(
            uploaded_file=io.BytesIO(_wav_bytes()),
            filename="into-a.wav",
            content_type="audio/wav",
            uploaded_by=user,
            organization=orgs["alpha"],
        )


@pytest.mark.django_db
def test_api_editor_cannot_approve(orgs):
    media = _media_in_org(orgs["alpha"], username="api_ed_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )

    editor = User.objects.create_user(username="api_editor", password="pass")
    _membership(editor, orgs["alpha"], TuringRole.EDITOR)
    client = APIClient()
    client.force_authenticate(user=editor)
    response = client.post(f"/api/turing/v1/transcripts/{transcript.id}/approve/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_api_reviewer_can_approve(orgs):
    media = _media_in_org(orgs["alpha"], username="api_rev_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )

    reviewer = User.objects.create_user(username="api_reviewer", password="pass")
    _membership(reviewer, orgs["alpha"], TuringRole.REVIEWER)
    client = APIClient()
    client.force_authenticate(user=reviewer)
    response = client.post(f"/api/turing/v1/transcripts/{transcript.id}/approve/")
    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == "approved"


@pytest.mark.django_db
def test_api_list_media_scoped_to_membership(orgs):
    media_a = _media_in_org(orgs["alpha"], username="list_a")
    _media_in_org(orgs["beta"], username="list_b")

    editor = User.objects.create_user(username="list_editor", password="pass")
    _membership(editor, orgs["alpha"], TuringRole.EDITOR)
    client = APIClient()
    client.force_authenticate(user=editor)
    response = client.get("/api/turing/v1/media/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert str(media_a.id) in ids
    assert len(ids) == 1


@pytest.mark.django_db
def test_api_cannot_create_job_for_foreign_media(orgs):
    media_b = _media_in_org(orgs["beta"], username="foreign_media")
    editor = User.objects.create_user(username="foreign_ed", password="pass")
    _membership(editor, orgs["alpha"], TuringRole.ADMIN)
    # Admin of alpha still cannot see beta media via scoped queryset
    client = APIClient()
    client.force_authenticate(user=editor)
    response = client.post(
        "/api/turing/v1/jobs/",
        {"media_id": str(media_b.id), "language_code": "en", "auto_enqueue": False},
        format="json",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_api_editor_can_submit_review_but_not_approve(orgs):
    media = _media_in_org(orgs["alpha"], username="submit_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )
    editor = User.objects.create_user(username="submit_editor", password="pass")
    _membership(editor, orgs["alpha"], TuringRole.EDITOR)
    client = APIClient()
    client.force_authenticate(user=editor)
    submit = client.post(f"/api/turing/v1/transcripts/{transcript.id}/submit_review/")
    assert submit.status_code == 200
    deny = client.post(f"/api/turing/v1/transcripts/{transcript.id}/approve/")
    assert deny.status_code == 403


@pytest.mark.django_db
def test_get_user_role_uses_org_membership(orgs):
    user = User.objects.create_user(username="multi", password="pass")
    _membership(user, orgs["alpha"], TuringRole.EDITOR)
    _membership(user, orgs["beta"], TuringRole.VIEWER)
    assert get_user_role(user, organization=orgs["alpha"]) == TuringRole.EDITOR
    assert get_user_role(user, organization=orgs["beta"]) == TuringRole.VIEWER
    # Global fallback: highest privilege
    assert get_user_role(user) == TuringRole.EDITOR


# --- Phase 2.9.2 ---


@pytest.mark.django_db
def test_organization_fk_required_on_media(orgs):
    from django.db import IntegrityError

    with pytest.raises((IntegrityError, ValueError, TypeError)):
        MediaAsset.objects.create(
            source_type="upload",
            organization=None,
        )


@pytest.mark.django_db
def test_service_edit_denied_without_capability(orgs):
    from turing.domain.exceptions import PermissionDeniedError

    media = _media_in_org(orgs["alpha"], username="edit_deny_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )
    segment = transcript.segments.get()
    viewer = User.objects.create_user(username="viewer_edit", password="pass")
    _membership(viewer, orgs["alpha"], TuringRole.VIEWER)
    with pytest.raises(PermissionDeniedError):
        TranscriptService().update_segment(
            segment=segment, text="Nope", edited_by=viewer
        )
    with pytest.raises(PermissionDeniedError):
        TranscriptService().approve(transcript=transcript, approved_by=viewer)


@pytest.mark.django_db
def test_admin_staff_viewer_cannot_change_or_approve(orgs):
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from turing.admin.job import ProcessingJobAdmin
    from turing.admin.media import MediaAssetAdmin
    from turing.admin.transcript import TranscriptAdmin

    media = _media_in_org(orgs["alpha"], username="adm_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )
    staff = User.objects.create_user(
        username="staff_viewer", password="pass", is_staff=True
    )
    _membership(staff, orgs["alpha"], TuringRole.VIEWER)
    request = RequestFactory().get("/")
    request.user = staff
    site = AdminSite()

    t_admin = TranscriptAdmin(Transcript, site)
    assert t_admin.has_view_permission(request, transcript)
    assert not t_admin.has_change_permission(request, transcript)
    assert not t_admin.has_delete_permission(request, transcript)

    m_admin = MediaAssetAdmin(MediaAsset, site)
    assert m_admin.has_view_permission(request, media)
    assert not m_admin.has_change_permission(request, media)

    j_admin = ProcessingJobAdmin(ProcessingJob, site)
    assert j_admin.has_view_permission(request, job)
    assert not j_admin.has_change_permission(request, job)


@pytest.mark.django_db
def test_admin_staff_without_membership_denied(orgs):
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from turing.admin.transcript import TranscriptAdmin

    media = _media_in_org(orgs["alpha"], username="adm2_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )
    staff = User.objects.create_user(
        username="staff_orphan", password="pass", is_staff=True
    )
    request = RequestFactory().get("/")
    request.user = staff
    t_admin = TranscriptAdmin(Transcript, AdminSite())
    assert not t_admin.has_module_permission(request)
    assert not t_admin.has_view_permission(request, transcript)
    assert not t_admin.has_change_permission(request, transcript)


@pytest.mark.django_db
def test_unique_constraint_speaker_label(orgs):
    from django.db import IntegrityError

    from turing.models import Speaker

    media = _media_in_org(orgs["alpha"], username="uniq_media")
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = TranscriptService().persist_from_provider(
        job=job,
        normalized=NormalizedTranscript(
            full_text="Hi",
            language_code="en",
            confidence_avg=0.8,
            segments=[
                NormalizedSegment(
                    sequence=0,
                    start_ms=0,
                    end_ms=100,
                    text="Hi",
                    confidence=0.8,
                    words=[],
                )
            ],
            speakers=[],
            raw={},
        ),
    )
    Speaker.objects.create(transcript=transcript, speaker_label="S1")
    with pytest.raises(IntegrityError):
        Speaker.objects.create(transcript=transcript, speaker_label="S1")
