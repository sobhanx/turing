"""MediaAsset Admin delete permissions with protected related objects."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.domain.enums import (
    AnalysisType,
    LogLevel,
    RevisionSource,
    TuringRole,
    UseCase,
)
from turing.models import (
    MediaAsset,
    Organization,
    ProcessingJob,
    ProcessingLog,
    Transcript,
    TranscriptAnalysis,
    TranscriptRevision,
    TuringMembership,
)
from turing.services.job_orchestrator import JobOrchestrator

User = get_user_model()


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Delete Org", slug="delete-org")


def _media_graph(org: Organization, *, username: str = "media-owner"):
    user = User.objects.create_user(username=username, password="pass")
    TuringMembership.objects.create(
        user=user,
        organization=org,
        role=TuringRole.ADMIN,
        is_active=True,
    )
    media = MediaAsset.objects.create(
        organization=org,
        uploaded_by=user,
        original_filename="delete-me.wav",
        use_case=UseCase.GENERIC,
        object_key="turing/delete-me.wav",
    )
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=user,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        language_code="en",
        full_text="hello",
        is_primary=True,
    )
    analysis = TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"text": "summary"},
        provider="test",
        model_name="test-model",
    )
    revision = TranscriptRevision.objects.create(
        transcript=transcript,
        revision_number=1,
        source=RevisionSource.PROVIDER,
        change_summary="initial",
        snapshot={"full_text": "hello"},
        created_by=user,
    )
    log = ProcessingLog.objects.create(
        job=job,
        level=LogLevel.INFO,
        message="started",
        context={},
    )
    return {
        "media": media,
        "job": job,
        "transcript": transcript,
        "analysis": analysis,
        "revision": revision,
        "log": log,
        "user": user,
    }


@pytest.mark.django_db
def test_superuser_can_delete_media_with_protected_related_objects(client, org):
    graph = _media_graph(org, username="su-media")
    media = graph["media"]
    superuser = User.objects.create_superuser("su-delete", "su@example.com", "pass")
    client.force_login(superuser)

    url = reverse("admin:turing_mediaasset_delete", args=[media.pk])
    confirm = client.get(url)
    assert confirm.status_code == 200
    body = confirm.content.decode()
    assert "doesn't have permission to delete" not in body
    assert "does not have permission to delete" not in body
    assert "Cannot delete" not in body

    deleted = client.post(url, {"post": "yes"})
    assert deleted.status_code == 302
    assert not MediaAsset.objects.filter(pk=media.pk).exists()
    assert not ProcessingJob.objects.filter(pk=graph["job"].pk).exists()
    assert not Transcript.objects.filter(pk=graph["transcript"].pk).exists()
    assert not TranscriptAnalysis.objects.filter(pk=graph["analysis"].pk).exists()
    assert not TranscriptRevision.objects.filter(pk=graph["revision"].pk).exists()
    assert not ProcessingLog.objects.filter(pk=graph["log"].pk).exists()


@pytest.mark.django_db
def test_staff_cannot_delete_media_when_protected_related_objects_exist(client, org):
    graph = _media_graph(org, username="staff-media")
    media = graph["media"]
    staff = User.objects.create_user(
        username="staff-delete",
        password="pass",
        is_staff=True,
        is_superuser=False,
    )
    TuringMembership.objects.create(
        user=staff,
        organization=org,
        role=TuringRole.ADMIN,
        is_active=True,
    )
    client.force_login(staff)

    url = reverse("admin:turing_mediaasset_delete", args=[media.pk])
    confirm = client.get(url)
    assert confirm.status_code == 200
    body = confirm.content.decode()
    assert "doesn't have permission to delete" in body or "does not have permission to delete" in body
    assert "Transcript analysis" in body
    assert "Transcript revision" in body
    assert "Processing log" in body

    blocked = client.post(url, {"post": "yes"})
    assert blocked.status_code == 403
    assert MediaAsset.objects.filter(pk=media.pk).exists()
    assert TranscriptAnalysis.objects.filter(pk=graph["analysis"].pk).exists()
    assert TranscriptRevision.objects.filter(pk=graph["revision"].pk).exists()
    assert ProcessingLog.objects.filter(pk=graph["log"].pk).exists()


@pytest.mark.django_db
def test_append_only_admins_delete_only_for_superuser():
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from turing.admin.analysis import TranscriptAnalysisAdmin
    from turing.admin.job import ProcessingLogAdmin
    from turing.admin.transcript import TranscriptRevisionAdmin

    site = AdminSite()
    factory = RequestFactory()
    su = User.objects.create_superuser("su-ro", "su-ro@example.com", "pass")
    staff = User.objects.create_user(
        username="staff-ro", password="pass", is_staff=True, is_superuser=False
    )

    for admin_cls, model in (
        (TranscriptAnalysisAdmin, TranscriptAnalysis),
        (TranscriptRevisionAdmin, TranscriptRevision),
        (ProcessingLogAdmin, ProcessingLog),
    ):
        ma = admin_cls(model, site)
        su_req = factory.get("/")
        su_req.user = su
        staff_req = factory.get("/")
        staff_req.user = staff
        assert ma.has_add_permission(su_req) is False
        assert ma.has_change_permission(su_req) is False
        assert ma.has_delete_permission(su_req) is True
        assert ma.has_delete_permission(staff_req) is False
