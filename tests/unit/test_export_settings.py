from __future__ import annotations

"""Transcript export settings (Admin) + shared visibility for PDF/DOCX."""

import io
import zipfile
from datetime import datetime, timezone

import pytest
from django.contrib.auth import get_user_model

from turing.domain.enums import AnalysisType, TranscriptStatus, TuringRole, UseCase
from turing.models import (
    Organization,
    Speaker,
    Transcript,
    TranscriptAnalysis,
    TranscriptExportSettings,
    TranscriptSegment,
    TuringMembership,
)
from turing.services.export import ExportService
from turing.services.export import labels as L
from turing.services.export.context import (
    apply_settings_to_document,
    cover_rows_for,
    default_visibility,
    meeting_info_rows_for,
    visibility_from_settings,
)
from turing.services.export.dates import (
    format_gregorian_date,
    format_persian_date,
    gregorian_to_jalali,
    to_tehran,
)
from turing.services.export.document import ActionItem, ExportDocument, SpeakerTurn
from turing.services.export.docx_exporter import DOCXExporter
from turing.services.export.pdf import PDFExporter
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService

User = get_user_model()


@pytest.fixture
def export_cfg_setup(db):
    org = Organization.get_default()
    viewer = User.objects.create_user("export-cfg-viewer", password="pass")
    TuringMembership.objects.create(
        user=viewer, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="settings-meeting.wav",
        use_case=UseCase.MEETING,
        organization=org,
        uploaded_by=None,
    )
    media.duration_ms = 125000
    media.save(update_fields=["duration_ms", "updated_at"])
    job = JobOrchestrator().create_transcription_job(
        media=media,
        created_by=None,
        language_code="en",
        auto_enqueue=False,
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        status=TranscriptStatus.APPROVED,
        language_code="en",
        full_text="",
        word_count=5,
    )
    speaker = Speaker.objects.create(
        transcript=transcript, speaker_label="S1", speaker_name="Alex"
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=0,
        text="Hello from the meeting.",
        start_ms=0,
        end_ms=2000,
    )
    TranscriptService().recompute_full_text(transcript)
    transcript.save(update_fields=["full_text", "updated_at"])

    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "We reviewed the roadmap.", "main_points": ["Ship Q3"]},
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.TOPICS,
        content=["roadmap", "budget"],
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[{"task": "Send plan", "owner": "Alex", "deadline": None}],
        provider="fake",
        model_name="fake-v1",
    )

    settings = TranscriptExportSettings.get_global()
    return {
        "org": org,
        "viewer": viewer,
        "transcript": transcript,
        "settings": settings,
    }


@pytest.mark.django_db
def test_default_export_configuration_flags(export_cfg_setup):
    settings = TranscriptExportSettings.get_global()
    assert settings.show_meeting_title is True
    assert settings.show_persian_date is True
    assert settings.show_gregorian_date is True
    assert settings.show_duration is True
    assert settings.show_speakers is True
    assert settings.show_full_transcript is True
    assert settings.show_timeline is True
    assert settings.show_provider is False
    assert settings.show_ai_summary is False
    assert settings.show_key_topics is False
    assert settings.show_action_items is False
    assert settings.show_decisions is False
    assert settings.show_keywords is False
    vis = default_visibility()
    assert vis.show_ai_summary is False
    assert vis.show_provider is False
    assert vis.show_full_transcript is True


@pytest.mark.django_db
def test_admin_configuration_changes_affect_visibility(export_cfg_setup):
    settings = export_cfg_setup["settings"]
    settings.show_provider = True
    settings.show_ai_summary = True
    settings.show_meeting_title = False
    settings.save()
    settings.refresh_from_db()
    vis = visibility_from_settings(settings)
    assert vis.show_provider is True
    assert vis.show_ai_summary is True
    assert vis.show_meeting_title is False


@pytest.mark.django_db
def test_disabled_sections_not_in_meeting_rows(export_cfg_setup):
    transcript = export_cfg_setup["transcript"]
    settings = export_cfg_setup["settings"]
    settings.show_provider = False
    settings.show_ai_summary = False
    settings.show_key_topics = False
    settings.show_action_items = False
    settings.show_decisions = False
    settings.show_keywords = False
    settings.save()

    doc = ExportService().build_document(transcript)
    labels = [label for label, _ in doc.meeting_info_rows()]
    assert L.LABEL_PROVIDER not in labels
    assert doc.visibility.show_ai_summary is False
    assert L.LABEL_PROVIDER not in dict(cover_rows_for(doc))
    assert L.SECTION_EXECUTIVE_SUMMARY not in [r[0] for r in meeting_info_rows_for(doc)]


@pytest.mark.django_db
def test_enabled_sections_appear_in_pdf_and_docx(export_cfg_setup):
    transcript = export_cfg_setup["transcript"]
    settings = export_cfg_setup["settings"]
    settings.show_provider = True
    settings.show_ai_summary = True
    settings.show_key_topics = True
    settings.show_action_items = True
    settings.show_decisions = True
    settings.show_keywords = True
    settings.save()

    doc = ExportService().build_document(transcript)
    labels = [label for label, _ in doc.meeting_info_rows()]
    assert L.LABEL_PROVIDER in labels
    assert doc.summary
    assert doc.topics
    assert doc.action_items
    assert doc.decisions

    pdf_buf = io.BytesIO()
    PDFExporter().write(doc, pdf_buf)
    assert pdf_buf.getvalue().startswith(b"%PDF")

    docx_buf = io.BytesIO()
    DOCXExporter().write(doc, docx_buf)
    with zipfile.ZipFile(io.BytesIO(docx_buf.getvalue())) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert L.SECTION_EXECUTIVE_SUMMARY in xml
    assert L.SECTION_KEY_TOPICS in xml
    assert L.SECTION_ACTION_ITEMS in xml
    assert L.SECTION_DECISIONS in xml
    assert L.SECTION_KEYWORDS in xml
    assert L.LABEL_PROVIDER in xml
    assert "We reviewed the roadmap." in xml


@pytest.mark.django_db
def test_pdf_and_docx_respect_same_configuration(export_cfg_setup):
    transcript = export_cfg_setup["transcript"]
    settings = export_cfg_setup["settings"]
    settings.show_provider = False
    settings.show_ai_summary = False
    settings.show_full_transcript = True
    settings.save()

    doc = ExportService().build_document(transcript)
    assert doc.visibility.show_provider is False
    assert doc.visibility.show_ai_summary is False

    pdf_data = b"".join(
        ExportService().export_transcript(
            transcript, "pdf", user=export_cfg_setup["viewer"]
        ).chunks
    )
    docx_data = b"".join(
        ExportService().export_transcript(
            transcript, "docx", user=export_cfg_setup["viewer"]
        ).chunks
    )
    assert pdf_data.startswith(b"%PDF")
    with zipfile.ZipFile(io.BytesIO(docx_data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert L.SECTION_EXECUTIVE_SUMMARY not in xml
    assert L.LABEL_PROVIDER not in xml
    assert "Hello from the meeting." in xml


@pytest.mark.django_db
def test_timezone_conversion_tehran_and_jalali():
    utc_dt = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
    local = to_tehran(utc_dt)
    assert local is not None
    assert str(local.tzinfo) == "Asia/Tehran"
    # 12:00 UTC → 15:30 IRST (or 16:30 IRDT depending on DST); date stays 2026-03-20 in Tehran
    assert format_gregorian_date(utc_dt) == "2026-03-20"
    jy, jm, jd = gregorian_to_jalali(2026, 3, 20)
    assert (jy, jm, jd) == (1404, 12, 29)
    assert format_persian_date(utc_dt) == "1404/12/29"


@pytest.mark.django_db
def test_resolve_for_organization_falls_back_to_global(export_cfg_setup):
    org = export_cfg_setup["org"]
    global_settings = TranscriptExportSettings.get_global()
    resolved = TranscriptExportSettings.resolve_for_organization(org)
    assert resolved.pk == global_settings.pk


@pytest.mark.django_db
def test_disabled_ai_sections_omitted_from_pdf_and_docx(export_cfg_setup):
    transcript = export_cfg_setup["transcript"]
    settings = export_cfg_setup["settings"]
    settings.show_ai_summary = False
    settings.show_key_topics = False
    settings.show_action_items = False
    settings.show_decisions = False
    settings.show_keywords = False
    settings.show_full_transcript = True
    settings.save()

    doc = ExportService().build_document(transcript)
    assert doc.visibility.any_ai_section is False
    assert doc.summary == ""
    assert doc.topics == []
    assert doc.action_items == []
    assert doc.decisions == []
    assert doc.keywords == []

    docx_data = b"".join(
        ExportService()
        .export_transcript(transcript, "docx", user=export_cfg_setup["viewer"])
        .chunks
    )
    with zipfile.ZipFile(io.BytesIO(docx_data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    for heading in (
        L.SECTION_EXECUTIVE_SUMMARY,
        L.SECTION_KEY_TOPICS,
        L.SECTION_ACTION_ITEMS,
        L.SECTION_DECISIONS,
        L.SECTION_KEYWORDS,
    ):
        assert heading not in xml
    assert "Hello from the meeting." in xml
    assert "We reviewed the roadmap." not in xml

    pdf_data = b"".join(
        ExportService()
        .export_transcript(transcript, "pdf", user=export_cfg_setup["viewer"])
        .chunks
    )
    assert pdf_data.startswith(b"%PDF")


@pytest.mark.django_db
def test_enabled_ai_sections_appear_in_pdf_and_docx_exports(export_cfg_setup):
    transcript = export_cfg_setup["transcript"]
    settings = export_cfg_setup["settings"]
    settings.show_ai_summary = True
    settings.show_key_topics = True
    settings.show_action_items = True
    settings.show_decisions = True
    settings.show_keywords = True
    settings.save()

    doc = ExportService().build_document(transcript)
    assert doc.visibility.any_ai_section is True
    assert "We reviewed the roadmap." in doc.summary
    assert "Ship Q3" in doc.decisions
    assert "roadmap" in doc.topics
    assert "budget" in doc.keywords
    assert doc.action_items[0].task == "Send plan"

    docx_data = b"".join(
        ExportService()
        .export_transcript(transcript, "docx", user=export_cfg_setup["viewer"])
        .chunks
    )
    with zipfile.ZipFile(io.BytesIO(docx_data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert L.SECTION_EXECUTIVE_SUMMARY in xml
    assert L.SECTION_KEY_TOPICS in xml
    assert L.SECTION_ACTION_ITEMS in xml
    assert L.SECTION_DECISIONS in xml
    assert L.SECTION_KEYWORDS in xml
    assert "We reviewed the roadmap." in xml
    assert "Ship Q3" in xml
    assert "Send plan" in xml
    assert "roadmap" in xml

    pdf_buf = io.BytesIO()
    PDFExporter().write(doc, pdf_buf)
    assert pdf_buf.getvalue().startswith(b"%PDF")


@pytest.mark.django_db
def test_individual_ai_section_toggles_are_independent(export_cfg_setup):
    transcript = export_cfg_setup["transcript"]
    settings = export_cfg_setup["settings"]
    # Only decisions / key points
    settings.show_ai_summary = False
    settings.show_key_topics = False
    settings.show_action_items = False
    settings.show_decisions = True
    settings.show_keywords = False
    settings.save()

    doc = ExportService().build_document(transcript)
    assert doc.visibility.show_decisions is True
    assert doc.visibility.show_ai_summary is False
    # Intelligence still loads because at least one AI flag is on.
    assert "Ship Q3" in doc.decisions

    with zipfile.ZipFile(
        io.BytesIO(
            b"".join(
                ExportService()
                .export_transcript(transcript, "docx", user=export_cfg_setup["viewer"])
                .chunks
            )
        )
    ) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert L.SECTION_DECISIONS in xml
    assert "Ship Q3" in xml
    assert L.SECTION_EXECUTIVE_SUMMARY not in xml
    assert L.SECTION_KEY_TOPICS not in xml
    assert L.SECTION_ACTION_ITEMS not in xml
    assert L.SECTION_KEYWORDS not in xml


@pytest.mark.django_db
def test_export_settings_admin_exposes_ai_section_controls():
    from django.contrib.admin.sites import AdminSite

    from turing.admin.export_settings import (
        TranscriptExportSettingsAdmin,
        TranscriptExportSettingsForm,
    )

    form = TranscriptExportSettingsForm()
    for field in (
        "show_ai_summary",
        "show_key_topics",
        "show_action_items",
        "show_decisions",
        "show_keywords",
    ):
        assert field in form.fields
    assert form.fields["show_ai_summary"].label == "Executive Summary"
    assert form.fields["show_decisions"].label == "Decisions / Key Points"

    admin_obj = TranscriptExportSettingsAdmin(TranscriptExportSettings, AdminSite())
    ai_fieldset = next(
        fs for fs in admin_obj.fieldsets if fs[0] == "AI analysis sections"
    )
    assert ai_fieldset[1]["fields"] == (
        "show_ai_summary",
        "show_key_topics",
        "show_action_items",
        "show_decisions",
        "show_keywords",
    )
    assert "show_ai_summary" in admin_obj.list_display
    assert "show_keywords" in admin_obj.list_display


@pytest.mark.django_db
def test_apply_settings_hides_timeline_timestamps(export_cfg_setup):
    settings = export_cfg_setup["settings"]
    settings.show_timeline = False
    settings.save()
    base = ExportDocument(
        transcript_id="x",
        project_title="Org",
        transcript_title="Call",
        media_filename="call.wav",
        organization="Org",
        language_code="en",
        duration_display="01:00",
        generated_at=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
        speakers=["Alex"],
        turns=[SpeakerTurn("Alex", "Hi", start_display="00:10")],
        rtl=False,
    )
    doc = apply_settings_to_document(base, settings=settings)
    assert doc.turn_timestamp("00:10") == ""
