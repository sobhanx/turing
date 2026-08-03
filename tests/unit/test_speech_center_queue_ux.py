from __future__ import annotations

"""Speech Center queue cancel / pipeline / meetings cleanup tests."""

import io
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.domain.enums import JobStatus, UseCase
from turing.models import Organization, ProcessingJob
from turing.services.job_orchestrator import CELERY_TASK_IDS_KEY, JobOrchestrator
from turing.services.media import MediaService
from turing.tasks import transcription as transcription_tasks
from turing.ui.speech_center.presentation import can_show_cancel, job_pipeline_steps

User = get_user_model()


@pytest.fixture
def sc_user(db):
    return User.objects.create_superuser("ux-admin", "ux@example.com", "pass")


@pytest.fixture
def sc_client(client, sc_user):
    client.force_login(sc_user)
    return client


@pytest.fixture
def sc_media(db, sc_user):
    org = Organization.get_default()
    return MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="stuck.wav",
        use_case=UseCase.GENERIC,
        organization=org,
        uploaded_by=sc_user,
    )


@pytest.mark.django_db
def test_cancel_stuck_transcription_job(sc_client, sc_media, sc_user, monkeypatch):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    job.status = JobStatus.RUNNING
    job.external_job_id = "ext-stuck"
    job.options = {CELERY_TASK_IDS_KEY: ["task-a", "task-b"]}
    job.save(update_fields=["status", "external_job_id", "options", "updated_at"])

    revoked: list[tuple] = []

    class FakeControl:
        def revoke(self, task_id, terminate=False):
            revoked.append((task_id, terminate))

    monkeypatch.setattr(
        "celery.current_app.control",
        FakeControl(),
        raising=False,
    )
    # Patch where revoke imports current_app
    import celery

    monkeypatch.setattr(celery, "current_app", MagicMock(control=FakeControl()))

    # Re-bind revoke path used inside orchestrator
    orch = JobOrchestrator()

    def _revoke(job_obj):
        for tid in (job_obj.options or {}).get(CELERY_TASK_IDS_KEY) or []:
            revoked.append((tid, False))
        return len(revoked)

    monkeypatch.setattr(orch, "revoke_celery_tasks", _revoke)
    monkeypatch.setattr(
        "turing.services.job_orchestrator.JobOrchestrator.revoke_celery_tasks",
        lambda self, job_obj: _revoke(job_obj),
    )
    monkeypatch.setattr(
        "turing.services.job_orchestrator.JobOrchestrator.cancel_provider_job",
        lambda self, job_obj, **kwargs: True,
    )

    assert can_show_cancel(job) is True
    url = reverse("speech_center:cancel_job", args=[job.id])
    resp = sc_client.post(url)
    assert resp.status_code == 302
    assert resp["Location"].endswith(reverse("speech_center:queue"))

    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
    assert job.error_message == "Cancelled by user"
    assert ProcessingJob.objects.filter(pk=job.pk).exists()
    assert {t for t, _ in revoked} >= {"task-a", "task-b"}

    queue = sc_client.get(reverse("speech_center:queue"))
    body = queue.content.decode()
    assert "Cancelled" in body
    assert "Cancelled by user" in body
    assert "stuck.wav" in body
    assert "sc-queue-elapsed" in body


@pytest.mark.django_db
def test_cancelled_jobs_do_not_auto_retry(sc_media, sc_user, monkeypatch):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    JobOrchestrator().cancel(job)
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED

    enqueued: list[str] = []
    monkeypatch.setattr(
        JobOrchestrator,
        "enqueue",
        lambda self, job_obj, **kwargs: enqueued.append(str(job_obj.id)) or job_obj,
    )
    result = transcription_tasks._maybe_auto_retry(
        str(job.id), error_code="PROVIDER_NETWORK"
    )
    assert result == "cancelled"
    assert enqueued == []


@pytest.mark.django_db
def test_new_jobs_continue_while_another_is_cancelled(sc_media, sc_user, db):
    stuck = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    JobOrchestrator().cancel(stuck)

    other_media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio2"),
        filename="newer.wav",
        use_case=UseCase.GENERIC,
        organization=sc_media.organization,
        uploaded_by=sc_user,
    )
    newer = JobOrchestrator().create_transcription_job(
        media=other_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    stuck.refresh_from_db()
    newer.refresh_from_db()
    assert stuck.status == JobStatus.CANCELLED
    assert newer.status in {JobStatus.PENDING, JobStatus.QUEUED}
    assert can_show_cancel(newer) is True
    assert can_show_cancel(stuck) is False


@pytest.mark.django_db
def test_queue_hides_provider_code(sc_client, sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    assert job.provider_code
    sc_media.duration_ms = 125_000
    sc_media.save(update_fields=["duration_ms", "updated_at"])

    resp = sc_client.get(reverse("speech_center:queue"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert job.provider_code not in body
    assert "speechmatics" not in body.lower()
    assert "sc-queue-meta" in body
    assert "02:05" in body or resp.context["jobs"][0]["media_duration"] == "02:05"


@pytest.mark.django_db
def test_queue_shows_elapsed_timing_line(sc_client, sc_media, sc_user):
    from django.utils import timezone

    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    job.status = JobStatus.RUNNING
    job.started_at = timezone.now() - timezone.timedelta(minutes=12)
    job.save(update_fields=["status", "started_at", "updated_at"])

    resp = sc_client.get(reverse("speech_center:queue"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "sc-queue-elapsed" in body
    assert "data-sc-elapsed-since=" in body
    assert "12 minute" in body or "Processing" in body
    rows = resp.context["jobs"]
    assert rows[0]["timing_line"]
    assert "—" in rows[0]["timing_line"]


@pytest.mark.django_db
def test_pipeline_steps_exclude_analysis(sc_media, sc_user):
    job = JobOrchestrator().create_transcription_job(
        media=sc_media,
        created_by=sc_user,
        language_code="en",
        auto_enqueue=False,
    )
    labels = [step["label"] for step in job_pipeline_steps(job)]
    assert labels == [
        "Uploading",
        "Preparing media",
        "Speech recognition",
        "Transcript ready",
    ]
    assert not any("Analysis" in label for label in labels)
    assert not any("Export" in label for label in labels)


@pytest.mark.django_db
def test_meetings_hidden_from_nav_and_returns_404(sc_client):
    dash = sc_client.get(reverse("speech_center:dashboard"))
    assert reverse("speech_center:meetings") not in dash.content.decode()
    assert sc_client.get(reverse("speech_center:meetings")).status_code == 404


@pytest.mark.django_db
def test_transcript_detail_still_supports_manual_analysis(
    sc_client, sc_media, sc_user, monkeypatch
):
    from turing.domain.enums import TranscriptStatus
    from turing.models import Transcript
    from turing.services import ai_analysis_trigger as trigger

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
        full_text="Hello",
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "turing.tasks.analysis.generate_transcript_analysis.delay",
        lambda transcript_id: scheduled.append(transcript_id),
    )
    detail = sc_client.get(
        reverse("speech_center:transcript_detail", args=[transcript.id])
    )
    assert detail.status_code == 200
    assert "Generate AI Insights" in detail.content.decode() or "Generate Analysis" in detail.content.decode()

    resp = sc_client.post(
        reverse("speech_center:generate_ai_insights", args=[transcript.id])
    )
    assert resp.status_code == 302
    assert scheduled == [str(transcript.id)]
    assert trigger.get_trigger_state(str(transcript.id)) == trigger.STATE_GENERATING
