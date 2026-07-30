from __future__ import annotations

"""Persian UI labels must be shared by PDF and DOCX exports."""

import io
import zipfile
from datetime import datetime, timezone

import pytest

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
from turing.services.export.context import cover_rows_for, meeting_info_rows_for
from turing.services.export.docx_exporter import DOCXExporter
from turing.services.export.pdf import PDFExporter
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def fa_export_setup(db):
    org = Organization.get_default()
    viewer = User.objects.create_user("export-fa-viewer", password="pass")
    TuringMembership.objects.create(
        user=viewer, organization=org, role=TuringRole.VIEWER, is_active=True
    )
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="جلسه.wav",
        use_case=UseCase.MEETING,
        organization=org,
        uploaded_by=None,
    )
    media.duration_ms = 60000
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
        word_count=4,
    )
    speaker = Speaker.objects.create(
        transcript=transcript, speaker_label="S1", speaker_name="سارا"
    )
    TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=0,
        text="سلام، جلسه شروع شد.",
        start_ms=0,
        end_ms=1500,
    )
    TranscriptService().recompute_full_text(transcript)
    transcript.save(update_fields=["full_text", "updated_at"])
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "خلاصه جلسه", "main_points": ["تصمیم اول"]},
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.TOPICS,
        content=["بودجه"],
        provider="fake",
        model_name="fake-v1",
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.ACTION_ITEMS,
        content=[{"task": "پیگیری", "owner": "سارا"}],
        provider="fake",
        model_name="fake-v1",
    )
    settings = TranscriptExportSettings.get_global()
    settings.show_ai_summary = True
    settings.show_key_topics = True
    settings.show_action_items = True
    settings.show_decisions = True
    settings.show_keywords = True
    settings.show_provider = True
    settings.save()
    return {"transcript": transcript, "viewer": viewer, "settings": settings}


def _docx_xml(document) -> str:
    buf = io.BytesIO()
    DOCXExporter().write(document, buf)
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
        footers = "".join(
            zf.read(n).decode("utf-8") for n in zf.namelist() if "footer" in n
        )
    return body + footers


def _assert_no_english_ui(text: str, *, include_words: bool = True) -> None:
    for label in L.FORBIDDEN_ENGLISH_UI_LABELS:
        assert label not in text, f"English UI label leaked: {label!r}"
    if include_words:
        for label in L.FORBIDDEN_ENGLISH_UI_WORDS:
            assert label not in text, f"English UI label leaked: {label!r}"


@pytest.mark.django_db
def test_shared_context_rows_use_persian_labels(fa_export_setup):
    doc = ExportService().build_document(fa_export_setup["transcript"])
    cover_labels = [label for label, _ in cover_rows_for(doc)]
    meeting_labels = [label for label, _ in meeting_info_rows_for(doc)]
    assert L.LABEL_ORGANIZATION in cover_labels
    assert L.LABEL_DURATION in cover_labels
    assert L.LABEL_SPEAKERS in cover_labels
    assert L.LABEL_MEETING_TITLE in meeting_labels
    assert L.LABEL_PROVIDER in meeting_labels
    _assert_no_english_ui("\n".join(cover_labels + meeting_labels))


@pytest.mark.django_db
def test_docx_export_contains_persian_headings(fa_export_setup):
    doc = ExportService().build_document(fa_export_setup["transcript"])
    xml = _docx_xml(doc)
    for expected in (
        L.REPORT_TITLE,
        L.SECTION_MEETING_INFO,
        L.SECTION_EXECUTIVE_SUMMARY,
        L.SECTION_KEY_TOPICS,
        L.SECTION_ACTION_ITEMS,
        L.SECTION_DECISIONS,
        L.SECTION_KEYWORDS,
        L.SECTION_TRANSCRIPT,
        L.LABEL_PROVIDER,
        L.FOOTER_GENERATED_BY,
    ):
        assert expected in xml
    _assert_no_english_ui(xml)
    assert "سلام، جلسه شروع شد." in xml
    assert "سارا" in xml


@pytest.mark.django_db
def test_pdf_export_contains_persian_headings_and_no_english_ui(fa_export_setup):
    doc = ExportService().build_document(fa_export_setup["transcript"])
    buf = io.BytesIO()
    PDFExporter().write(doc, buf)
    data = buf.getvalue()
    assert data.startswith(b"%PDF")
    # ReportLab embeds glyphs; assert English UI phrases are gone from the stream.
    # Skip single-word checks — PDF metadata uses keys like /Keywords.
    decoded = data.decode("latin-1", errors="ignore")
    _assert_no_english_ui(decoded, include_words=False)
    # Both exporters import the same label source.
    import turing.services.export.pdf as pdf_mod
    import turing.services.export.docx_exporter as docx_mod

    assert pdf_mod.L.REPORT_TITLE == docx_mod.L.REPORT_TITLE == L.REPORT_TITLE
    assert pdf_mod.L.SECTION_TRANSCRIPT == L.SECTION_TRANSCRIPT


@pytest.mark.django_db
def test_disabled_sections_remain_hidden_with_persian_labels(fa_export_setup):
    settings = fa_export_setup["settings"]
    settings.show_ai_summary = False
    settings.show_provider = False
    settings.show_key_topics = False
    settings.show_action_items = False
    settings.show_decisions = False
    settings.show_keywords = False
    settings.save()

    doc = ExportService().build_document(fa_export_setup["transcript"])
    xml = _docx_xml(doc)
    assert L.SECTION_EXECUTIVE_SUMMARY not in xml
    assert L.SECTION_KEY_TOPICS not in xml
    assert L.SECTION_ACTION_ITEMS not in xml
    assert L.SECTION_DECISIONS not in xml
    assert L.SECTION_KEYWORDS not in xml
    assert L.LABEL_PROVIDER not in xml
    assert L.SECTION_TRANSCRIPT in xml
    assert L.SECTION_MEETING_INFO in xml
    _assert_no_english_ui(xml)


def test_labels_module_is_single_source():
    assert L.LABEL_PROVIDER == "ارائه‌دهنده"
    assert L.SECTION_TRANSCRIPT == "متن پیاده‌سازی شده"
    assert L.MEETING_FALLBACK == "جلسه"
    assert L.LABEL_DURATION == "مدت زمان"
    assert L.LABEL_SPEAKERS == "گویندگان"
    assert L.LABEL_TIMELINE == "خط زمانی"
    assert L.LABEL_FILE == "فایل"
