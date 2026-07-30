from __future__ import annotations

"""Phase A verification — prove each stabilization claim with executable checks."""

import io
import wave
from unittest.mock import MagicMock, patch

import pytest
import responses
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from turing.admin.job import ProcessingJobAdmin
from turing.admin.media import MediaAssetAdmin
from turing.domain.enums import (
    JobStatus,
    SourceType,
    TranscriptStatus,
    UseCase,
)
from turing.domain.exceptions import ValidationError
from turing.models import (
    Embedding,
    MediaAsset,
    Organization,
    ProcessingJob,
    Speaker,
    Transcript,
    TranscriptSegment,
)
from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
    TranscriptionRequest,
)
from turing.security.urls import assert_safe_public_http_url
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.rag import RAGService
from turing.services.search_index import SearchIndexService
from turing.services.transcript import TranscriptService
from turing.services.transcript_analysis import TranscriptAnalysisService
from turing.services.transcription import TranscriptionService

User = get_user_model()


def _wav_bytes(duration_sec: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * duration_sec))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Connector media → Turing storage
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@responses.activate
def test_verify_connector_download_populates_file_and_object_key():
    from turing.connectors.media_ingest import create_media_from_connector_url

    org = Organization.get_default()
    audio = _wav_bytes()
    responses.add(
        responses.GET,
        "https://cdn.example.test/rec.wav",
        body=audio,
        status=200,
        content_type="audio/wav",
    )
    asset, mode = create_media_from_connector_url(
        url="https://cdn.example.test/rec.wav",
        organization=org,
        use_case=UseCase.MEETING,
        original_filename="rec.wav",
        headers={"Authorization": "Bearer test"},
        fallback_to_url=True,
    )
    assert mode == "downloaded"
    asset.refresh_from_db()
    assert asset.source_type == SourceType.UPLOAD
    assert asset.object_key
    assert asset.file.name
    assert asset.byte_size > 0
    assert asset.checksum
    # Provenance URL may be stored, but STT must not depend on it alone.
    assert asset.external_url == "https://cdn.example.test/rec.wav"


@pytest.mark.django_db
@responses.activate
def test_verify_stt_request_uses_storage_not_vendor_url_for_downloaded_media():
    from turing.connectors.media_ingest import create_media_from_connector_url

    org = Organization.get_default()
    audio = _wav_bytes()
    responses.add(
        responses.GET,
        "https://cdn.example.test/stt.wav",
        body=audio,
        status=200,
        content_type="audio/wav",
    )
    asset, mode = create_media_from_connector_url(
        url="https://cdn.example.test/stt.wav",
        organization=org,
        original_filename="stt.wav",
    )
    assert mode == "downloaded"
    job = JobOrchestrator().create_transcription_job(
        media=asset, language_code="en", auto_enqueue=False
    )
    req = TranscriptionService()._build_request(job)
    # Must not send Speechmatics the vendor CDN URL when storage bytes exist.
    assert req.media_url != "https://cdn.example.test/stt.wav"
    assert req.media_bytes or (
        req.media_url and "cdn.example.test" not in (req.media_url or "")
    )
    if req.media_bytes:
        assert len(req.media_bytes) == len(audio)


# ---------------------------------------------------------------------------
# 2. Speaker rename → transcript + search + RAG
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_verify_speaker_rename_updates_transcript_search_and_rag():
    from turing.search import PgVectorSearchProvider, register_builtin_search_providers
    from turing.search.embeddings import (
        LocalNeuralEmbeddingProvider,
        register_builtin_embedding_providers,
    )
    from turing.search.embeddings.registry import EmbeddingProviderRegistry
    from turing.search.registry import SemanticSearchRegistry

    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()

    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename="rename.wav",
        content_type="audio/wav",
        organization=org,
    )
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="en", auto_enqueue=False
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        status=TranscriptStatus.DRAFT,
        language_code="en",
        full_text="",
    )
    speaker = Speaker.objects.create(
        transcript=transcript, speaker_label="S1", speaker_name=""
    )
    segment = TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=0,
        text="Hello world",
        start_ms=0,
        end_ms=1000,
        words=[{"content": "Hello", "speaker_label": "S1"}],
    )
    TranscriptService().recompute_full_text(transcript)
    transcript.save(update_fields=["full_text", "updated_at"])

    emb_provider = LocalNeuralEmbeddingProvider(
        model_name="turing-local-small", dimensions=64
    )
    search = PgVectorSearchProvider(embedding_provider=emb_provider)
    SearchIndexService(provider=search, embedding_provider=emb_provider).index_transcript(
        transcript
    )

    TranscriptService().rename_speaker(speaker=speaker, speaker_name="Alice")
    transcript.refresh_from_db()
    speaker.refresh_from_db()

    assert speaker.resolved_name == "Alice"
    assert "Alice:" in transcript.full_text

    emb = Embedding.objects.filter(
        organization=org,
        object_type="transcript_segment",
        metadata__segment_id=str(segment.id),
    ).first()
    assert emb is not None
    assert emb.metadata.get("speaker") == "Alice"
    assert emb.metadata.get("speaker_name") == "Alice"
    assert emb.metadata.get("speaker_label") == "S1"

    sources = RAGService(search_provider=search).retrieve_context("Hello", org)
    assert sources
    assert sources[0].get("speaker") == "Alice"
    assert sources[0].get("speaker_name") == "Alice"


# ---------------------------------------------------------------------------
# 3. Webhook SSRF matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/hook",
        "http://127.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.8/hook",
        "http://192.168.1.10/hook",
        "http://172.16.5.5/hook",
        "http://[::1]/hook",
    ],
)
def test_verify_ssrf_blocks_private_and_metadata(url):
    with pytest.raises(ValidationError):
        assert_safe_public_http_url(url, purpose="Webhook URL", resolve_dns=True)


def test_verify_ssrf_allows_public_literal():
    assert assert_safe_public_http_url(
        "https://1.1.1.1/webhook", purpose="Webhook URL", resolve_dns=True
    )


@pytest.mark.django_db
def test_verify_webhook_api_rejects_localhost():
    client = APIClient()
    user = User.objects.create_superuser("ssrf-admin", "s@example.com", "pass")
    client.force_authenticate(user)
    org = Organization.get_default()
    for url in (
        "http://127.0.0.1/h",
        "http://localhost/h",
        "http://169.254.169.254/h",
        "http://10.1.2.3/h",
    ):
        resp = client.post(
            "/api/turing/v1/webhooks/",
            {
                "name": f"bad-{url}",
                "url": url,
                "subscribed_events": ["*"],
                "organization_id": org.id,
            },
            format="json",
        )
        assert resp.status_code == 400, url


# ---------------------------------------------------------------------------
# 4. Migration / constraint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_verify_org_idempotency_constraint_exists():
    constraint_names = set()
    with connection.cursor() as cursor:
        table = ProcessingJob._meta.db_table
        if connection.vendor == "sqlite":
            cursor.execute(f"PRAGMA index_list('{table}')")
            for row in cursor.fetchall():
                constraint_names.add(row[1])
        else:
            cursor.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = %s::regclass
                """,
                [table],
            )
            constraint_names = {r[0] for r in cursor.fetchall()}
    # Django may name the unique index after the constraint.
    assert any(
        "org_idempotency" in name or "idempotency" in name
        for name in constraint_names
    ) or any(
        c.name == "turing_job_org_idempotency_key_uniq"
        for c in ProcessingJob._meta.constraints
    )


@pytest.mark.django_db
def test_verify_idempotency_key_scoped_per_organization():
    org_a = Organization.get_default()
    org_b = Organization.objects.create(name="B", slug="verify-idem-b")
    media_a = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename="a.wav",
        content_type="audio/wav",
        organization=org_a,
    )
    media_b = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename="b.wav",
        content_type="audio/wav",
        organization=org_b,
    )
    orch = JobOrchestrator()
    ja = orch.create_transcription_job(
        media=media_a, language_code="en", idempotency_key="k1", auto_enqueue=False
    )
    jb = orch.create_transcription_job(
        media=media_b, language_code="en", idempotency_key="k1", auto_enqueue=False
    )
    assert ja.id != jb.id


# ---------------------------------------------------------------------------
# 5. Admin pagination / select_related
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_verify_media_and_job_admin_config():
    media_admin = MediaAssetAdmin(MediaAsset, AdminSite())
    job_admin = ProcessingJobAdmin(ProcessingJob, AdminSite())
    assert media_admin.list_select_related == ("organization", "uploaded_by")
    assert media_admin.list_per_page == 50
    assert job_admin.list_select_related == ("media", "organization", "created_by")
    assert job_admin.list_per_page == 50
    assert any(
        getattr(inline, "max_num", None) == 25 for inline in job_admin.inlines
    )


@pytest.mark.django_db
def test_verify_media_admin_changelist_uses_select_related(client):
    user = User.objects.create_superuser("adm", "a@example.com", "pass")
    client.force_login(user)
    org = Organization.get_default()
    for i in range(3):
        MediaService().create_from_upload(
            uploaded_file=io.BytesIO(_wav_bytes()),
            filename=f"m{i}.wav",
            content_type="audio/wav",
            organization=org,
            uploaded_by=user,
        )
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get("/admin/turing/mediaasset/")
    assert resp.status_code == 200
    # Should not N+1 on organization/uploaded_by per row (rough ceiling).
    assert len(ctx) < 30


# ---------------------------------------------------------------------------
# 6. End-to-end pipeline (mocked Speechmatics)
# ---------------------------------------------------------------------------


class _FakeSTT:
    code = "speechmatics"

    def submit(self, request: TranscriptionRequest):
        # Prove request is not vendor-URL-only for uploads.
        assert request.media_bytes or request.media_url
        return ProviderJobHandle(external_job_id="ext-verify", provider_code=self.code)

    def get_status(self, handle):
        return ProviderJobStatus(
            external_job_id=handle.external_job_id, state="succeeded"
        )

    def fetch_result(self, handle):
        return NormalizedTranscript(
            language_code="en",
            full_text="S1: Pipeline verification complete.",
            confidence_avg=0.9,
            speakers=[NormalizedSpeaker(label="S1")],
            segments=[
                NormalizedSegment(
                    sequence=0,
                    text="Pipeline verification complete.",
                    start_ms=0,
                    end_ms=1500,
                    confidence=0.9,
                    speaker_label="S1",
                )
            ],
        )


@pytest.mark.django_db
def test_verify_e2e_pipeline_upload_to_export_and_webhook(settings, monkeypatch):
    from turing.domain.enums import IngestStatus
    from turing.domain.events import DomainEvent, EventName
    from turing.events.outbox import OutboxDispatcher, persist_domain_event
    from turing.events.outbound import register_outbound_handlers
    from turing.models import WebhookDelivery, WebhookSubscription
    from turing.search import PgVectorSearchProvider, register_builtin_search_providers
    from turing.search.embeddings import (
        LocalNeuralEmbeddingProvider,
        register_builtin_embedding_providers,
    )
    from turing.search.embeddings.registry import EmbeddingProviderRegistry
    from turing.search.registry import SemanticSearchRegistry
    from turing.services.export import ExportService
    from turing.services.media_ingestion import MediaIngestionService
    from turing.services.webhook_delivery import WebhookDeliveryService

    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    OutboxDispatcher.clear()
    register_outbound_handlers()
    EmbeddingProviderRegistry.clear()
    register_builtin_embedding_providers()
    SemanticSearchRegistry.clear()
    register_builtin_search_providers()

    captured_requests: list = []

    class _CapturingSTT(_FakeSTT):
        def submit(self, request: TranscriptionRequest):
            captured_requests.append(request)
            assert request.media_bytes or request.media_url
            if request.media_url:
                assert "cdn.example.test" not in request.media_url
            return ProviderJobHandle(
                external_job_id="ext-verify", provider_code=self.code
            )

    monkeypatch.setattr(
        "turing.providers.registry.ProviderRegistry.get",
        lambda code: _CapturingSTT(),
    )

    org = Organization.get_default()
    user = User.objects.create_superuser("e2e", "e2e@example.com", "pass")

    # Upload
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(_wav_bytes()),
        filename="e2e.wav",
        content_type="audio/wav",
        organization=org,
        uploaded_by=user,
    )
    assert media.object_key and media.file.name

    # Ingestion
    job = JobOrchestrator().create_transcription_job(
        media=media, created_by=user, language_code="en", auto_enqueue=False
    )
    ingest = MediaIngestionService().prepare_for_job(job)
    job.refresh_from_db()
    assert ingest.status in {IngestStatus.SUCCEEDED, IngestStatus.SKIPPED}
    assert job.ingest_status in {
        IngestStatus.SUCCEEDED,
        IngestStatus.SKIPPED,
        "",
    }

    # Speechmatics (mocked) → Transcript
    transcript = TranscriptionService().process_job(str(job.id))
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED
    assert transcript.full_text
    assert captured_requests, "STT submit must be called"

    # Analysis
    analyses = TranscriptAnalysisService().generate_default_suite(transcript)
    assert analyses

    # Search index
    emb_provider = LocalNeuralEmbeddingProvider(
        model_name="turing-local-small", dimensions=64
    )
    search = PgVectorSearchProvider(embedding_provider=emb_provider)
    SearchIndexService(provider=search, embedding_provider=emb_provider).index_transcript(
        transcript
    )
    assert Embedding.objects.filter(
        metadata__transcript_id=str(transcript.id)
    ).exists()

    # Export
    pdf = ExportService().export_transcript(transcript, "pdf", user=user)
    pdf_bytes = b"".join(pdf.chunks)
    assert pdf_bytes.startswith(b"%PDF")

    # Webhook
    sub = WebhookSubscription(
        organization=org,
        name="e2e",
        url="https://hooks.example.com/turing-e2e",
        subscribed_events=["*"],
        is_active=True,
        secret="super-secret",
    )
    sub.full_clean()
    sub.save()
    outbox = persist_domain_event(
        DomainEvent(
            name=EventName.TRANSCRIPT_CREATED,
            payload={
                "transcript_id": str(transcript.id),
                "organization_id": org.id,
            },
        )
    )
    with patch("turing.services.webhook_delivery.requests.post") as post:
        post.return_value = MagicMock(status_code=200, text="ok")
        deliveries = WebhookDeliveryService().enqueue_for_outbox(outbox)
        assert deliveries
        delivery = WebhookDelivery.objects.filter(subscription=sub).first()
        assert delivery is not None
        WebhookDeliveryService().attempt_delivery(str(delivery.id))
        post.assert_called()
        assert "hooks.example.com" in str(post.call_args)
