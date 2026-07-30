from __future__ import annotations

"""On-demand transcript export (PDF / DOCX) — additive Export layer."""

import io
import zipfile
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from turing.domain.enums import TranscriptStatus, TuringRole, UseCase
from turing.models import (
    Organization,
    Speaker,
    Transcript,
    TranscriptSegment,
    TuringMembership,
)
from turing.services.export import ExportService
from turing.services.export import labels as L
from turing.services.export.document import ExportDocument, SpeakerTurn
from turing.services.export.pdf import PDFExporter
from turing.services.export.service import ensure_supported_format
from turing.services.export.text import is_rtl_language, prepare_visual_text
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService

User = get_user_model()
API_BASE = "/api/turing/v1/transcripts"


@pytest.fixture
def export_setup(db):
    org = Organization.get_default()
    other = Organization.objects.create(name="Other Org", slug="export-other")
    viewer = User.objects.create_user("export-viewer", password="pass")
    outsider = User.objects.create_user("export-outsider", password="pass")
    TuringMembership.objects.create(
        user=viewer, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    TuringMembership.objects.create(
        user=outsider, organization=other, role=TuringRole.VIEWER, is_active=True
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="meeting-fa.wav",
        use_case=UseCase.MEETING,
        organization=org,
        uploaded_by=None,
    )
    media.duration_ms = 125000
    media.save(update_fields=["duration_ms", "updated_at"])
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=None,
        language_code="fa",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        status=TranscriptStatus.APPROVED,
        language_code="fa",
        full_text="",
    )
    s1 = Speaker.objects.create(
        transcript=transcript, speaker_label="S1", speaker_name="علی"
    )
    s2 = Speaker.objects.create(
        transcript=transcript, speaker_label="S2", speaker_name="مریم"
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=s1,
        sequence=0,
        text="سلام، جلسه را شروع می‌کنیم.",
        start_ms=0,
        end_ms=2000,
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=s1,
        sequence=1,
        text="موضوع امروز قرارداد است.",
        start_ms=2000,
        end_ms=4000,
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=s2,
        sequence=2,
        text="موافقم، ادامه دهیم.",
        start_ms=4000,
        end_ms=6000,
    )
    TranscriptService().recompute_full_text(transcript)
    transcript.save(update_fields=["full_text", "updated_at"])
    return {
        "org": org,
        "viewer": viewer,
        "outsider": outsider,
        "transcript": transcript,
        "media": media,
    }


def _client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_format_export_body_speaker_blocks(export_setup):
    transcript = export_setup["transcript"]
    body = TranscriptService().format_export_body(transcript)
    assert "علی" in body
    assert "مریم" in body
    assert "سلام، جلسه را شروع می‌کنیم." in body
    # Merged consecutive S1 turns, blank-line separated speakers
    assert "علی\n\n" in body
    assert "\n\nمریم\n\n" in body
    assert "S1:" not in body


def test_rtl_helpers_persian():
    assert is_rtl_language("fa")
    assert is_rtl_language("fa-IR")
    assert not is_rtl_language("en")
    shaped = prepare_visual_text("سلام", rtl=True)
    assert shaped
    assert shaped != ""  # reshaped / reordered form


@pytest.mark.django_db
def test_pdf_export_unicode_rtl(export_setup):
    transcript = export_setup["transcript"]
    result = ExportService().export_transcript(
        transcript, "pdf", user=export_setup["viewer"]
    )
    data = b"".join(result.chunks)
    assert data.startswith(b"%PDF")
    assert result.content_type == "application/pdf"
    assert result.filename.endswith(".pdf")
    assert len(data) > 1000


@pytest.mark.django_db
def test_docx_export_unicode_rtl(export_setup):
    transcript = export_setup["transcript"]
    result = ExportService().export_transcript(
        transcript, "docx", user=export_setup["viewer"]
    )
    data = b"".join(result.chunks)
    assert data[:2] == b"PK"
    assert "wordprocessingml" in result.content_type
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "علی" in xml
    assert "مریم" in xml
    assert "سلام" in xml
    assert "w:bidi" in xml  # RTL paragraphs marked


@pytest.mark.django_db
def test_api_pdf_and_docx_endpoints(export_setup):
    client = _client_for(export_setup["viewer"])
    tid = export_setup["transcript"].id
    pdf = client.get(f"{API_BASE}/{tid}/export/pdf/")
    assert pdf.status_code == 200
    assert pdf["Content-Type"] == "application/pdf"
    assert "attachment" in pdf["Content-Disposition"]
    assert b"".join(pdf.streaming_content).startswith(b"%PDF")

    docx = client.get(f"{API_BASE}/{tid}/export/docx/")
    assert docx.status_code == 200
    assert b"".join(docx.streaming_content)[:2] == b"PK"


@pytest.mark.django_db
def test_export_authorization_org_boundary(export_setup):
    client = _client_for(export_setup["outsider"])
    tid = export_setup["transcript"].id
    resp = client.get(f"{API_BASE}/{tid}/export/pdf/")
    assert resp.status_code in {403, 404}


@pytest.mark.django_db
def test_export_missing_transcript(export_setup):
    client = _client_for(export_setup["viewer"])
    missing = uuid4()
    resp = client.get(f"{API_BASE}/{missing}/export/pdf/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_large_transcript_export_streams(export_setup):
    transcript = export_setup["transcript"]
    speaker = transcript.speakers.first()
    # Append many segments without loading all into one giant string in the test.
    TranscriptSegment.objects.bulk_create(
        [
            TranscriptSegment(
                transcript=transcript,
                speaker=speaker,
                sequence=100 + i,
                text=f"Segment number {i} with enough text for a longer document.",
                start_ms=10_000 + i * 100,
                end_ms=10_050 + i * 100,
            )
            for i in range(400)
        ]
    )
    result = ExportService().export_transcript(
        transcript, "pdf", user=export_setup["viewer"], chunk_size=8 * 1024
    )
    chunks = list(result.chunks)
    assert len(chunks) >= 1
    data = b"".join(chunks)
    assert data.startswith(b"%PDF")
    assert len(data) > 20_000


@pytest.mark.django_db
def test_unsupported_format_raises():
    with pytest.raises(Exception):
        ensure_supported_format("xlsx")


@pytest.mark.django_db
def test_speech_center_export_ui_and_download(export_setup, client):
    user = User.objects.create_superuser("export-staff", "e@example.com", "pass")
    client.force_login(user)
    tid = export_setup["transcript"].id
    detail = client.get(reverse("speech_center:transcript_detail", args=[tid]))
    assert detail.status_code == 200
    body = detail.content.decode()
    assert "Export" in body
    assert "Export PDF" in body
    assert "Export DOCX" in body

    pdf = client.get(
        reverse("speech_center:export_transcript", args=[tid, "pdf"])
    )
    assert pdf.status_code == 200
    assert b"".join(pdf.streaming_content).startswith(b"%PDF")


@pytest.mark.django_db
def test_pdf_exporter_includes_metadata_fields(export_setup):
    from datetime import datetime, timezone

    from turing.services.export.document import ActionItem

    doc = ExportDocument(
        transcript_id="x",
        project_title="Acme",
        transcript_title="Call",
        media_filename="call.wav",
        organization="Acme Org",
        language_code="en",
        duration_display="02:05",
        generated_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        speakers=["Alex"],
        turns=[SpeakerTurn("Alex", "Hello world", start_display="00:00")],
        body_text="Alex\n\nHello world",
        rtl=False,
        created_at_display="2026-07-29 10:00 UTC",
        provider="speechmatics",
        speaker_count=1,
        word_count=2,
        summary="We discussed the contract.",
        decisions=["Approve the proposal"],
        topics=["contract", "timeline"],
        keywords=["contract", "timeline"],
        action_items=[ActionItem(task="Send quote", owner="Alex")],
    )
    buf = io.BytesIO()
    PDFExporter().write(doc, buf)
    assert buf.getvalue().startswith(b"%PDF")


@pytest.mark.django_db
def test_export_document_includes_timed_turns_and_stats(export_setup):
    transcript = export_setup["transcript"]
    doc = ExportService().build_document(transcript)
    assert doc.rtl is True
    assert doc.speaker_count == 2
    assert doc.duration_display == "02:05"
    assert doc.turns
    assert doc.turns[0].start_display == "00:00"
    assert doc.turns[0].speaker_name == "علی"
    assert any(t.speaker_name == "مریم" for t in doc.turns)


@pytest.mark.django_db
def test_export_document_includes_intelligence(export_setup):
    from turing.domain.enums import AnalysisType
    from turing.models import TranscriptAnalysis, TranscriptExportSettings

    settings = TranscriptExportSettings.get_global()
    settings.show_ai_summary = True
    settings.show_key_topics = True
    settings.show_action_items = True
    settings.show_decisions = True
    settings.show_keywords = True
    settings.save()

    transcript = export_setup["transcript"]
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=transcript.organization,
        analysis_type=AnalysisType.SUMMARY,
        content={
            "summary": "جلسه درباره قرارداد بود.",
            "main_points": ["قرارداد تایید شد"],
        },
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=transcript.organization,
        analysis_type=AnalysisType.TOPICS,
        content=["قرارداد", "بودجه"],
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=transcript.organization,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[{"task": "ارسال پیشنهاد", "owner": "علی", "deadline": None}],
        provider="fake",
        model_name="fake-v1",
    )
    doc = ExportService().build_document(transcript)
    assert "قرارداد" in doc.summary
    assert "قرارداد تایید شد" in doc.decisions
    assert "بودجه" in doc.topics
    assert doc.action_items[0].task == "ارسال پیشنهاد"
    assert doc.action_items[0].owner == "علی"

    pdf = ExportService().export_transcript(
        transcript, "pdf", user=export_setup["viewer"]
    )
    assert b"".join(pdf.chunks).startswith(b"%PDF")

    docx = ExportService().export_transcript(
        transcript, "docx", user=export_setup["viewer"]
    )
    data = b"".join(docx.chunks)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        footer_names = [n for n in zf.namelist() if "footer" in n]
        footer_xml = "".join(zf.read(n).decode("utf-8") for n in footer_names)
    assert L.REPORT_TITLE in xml
    assert L.SECTION_EXECUTIVE_SUMMARY in xml
    assert L.SECTION_ACTION_ITEMS in xml
    assert L.FOOTER_GENERATED_BY in footer_xml
    assert "ارسال پیشنهاد" in xml
    assert "w:bidi" in xml
