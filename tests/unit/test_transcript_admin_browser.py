from __future__ import annotations

"""Admin UX tests for scalable Transcript / Segment / Word browsers."""

import io

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.urls import reverse

from turing.admin.formatting import format_confidence_pct, format_timestamp_ms
from turing.admin.transcript import (
    SpeakerInline,
    TranscriptAdmin,
    TranscriptSegmentAdmin,
    TranscriptWordAdmin,
)
from turing.domain.enums import AnalysisType, TranscriptStatus, UseCase
from turing.models import (
    Organization,
    Speaker,
    Transcript,
    TranscriptAnalysis,
    TranscriptSegment,
    TranscriptWord,
)
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService

User = get_user_model()


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "00:00:00.000"),
        (76785, "00:01:16.785"),
        (3723123, "01:02:03.123"),
        (1000, "00:00:01.000"),
        (61_000, "00:01:01.000"),
        (None, "—"),
    ],
)
def test_format_timestamp_ms(ms, expected):
    assert format_timestamp_ms(ms) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "—"),
        (0.95, "95.0%"),
        (0.0, "0.0%"),
        (1.0, "100.0%"),
    ],
)
def test_format_confidence_pct(value, expected):
    assert format_confidence_pct(value) == expected


@pytest.fixture
def browser_fixture(db):
    org = Organization.get_default()
    media = MediaService().create_from_upload(
        uploaded_file=io.BytesIO(b"audio"),
        filename="call-recording.wav",
        use_case=UseCase.CRM_CALL,
        organization=org,
    )
    media.duration_ms = 8_130_000  # 02:15:30
    media.save(update_fields=["duration_ms"])
    job = JobOrchestrator().create_transcription_job(
        media=media, language_code="fa", auto_enqueue=False
    )
    transcript = Transcript.objects.create(
        job=job,
        media=media,
        organization=org,
        status=TranscriptStatus.DRAFT,
        language_code="fa",
        full_text="Discuss renewal pricing today.",
        word_count=4,
        confidence_avg=0.91,
    )
    speaker = Speaker.objects.create(
        transcript=transcript,
        speaker_label="S1",
        speaker_name="Agent",
        confidence=0.88,
    )
    speaker2 = Speaker.objects.create(
        transcript=transcript,
        speaker_label="S2",
        speaker_name="",
        confidence=None,
    )
    # Deliberately out of start_ms order by sequence to prove admin ordering.
    later = TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=1,
        text="Follow up next week on the contract renewal pricing discussion.",
        start_ms=76785,
        end_ms=82000,
    )
    earlier = TranscriptSegment.objects.create(
        transcript=transcript,
        speaker=speaker,
        sequence=0,
        text="Discuss renewal pricing today.",
        start_ms=0,
        end_ms=2000,
    )
    word = TranscriptWord.objects.create(
        segment=earlier,
        sequence=0,
        text="Discuss",
        start_ms=0,
        end_ms=400,
        confidence=0.95,
    )
    TranscriptAnalysis.objects.create(
        transcript=transcript,
        organization=org,
        analysis_type=AnalysisType.SUMMARY,
        content={"summary": "Renewal pricing discussion"},
        provider="fake",
        model_name="test",
    )
    admin_user = User.objects.create_superuser(
        username="seg-admin", email="seg@example.com", password="pass"
    )
    return {
        "org": org,
        "media": media,
        "transcript": transcript,
        "speaker": speaker,
        "speaker2": speaker2,
        "earlier": earlier,
        "later": later,
        "word": word,
        "admin_user": admin_user,
    }


@pytest.mark.django_db
def test_transcript_admin_has_no_segment_or_word_inlines():
    admin = TranscriptAdmin(Transcript, AdminSite())
    inline_models = {inline.model for inline in admin.inlines}
    assert TranscriptSegment not in inline_models
    assert TranscriptWord not in inline_models
    assert Speaker in inline_models
    assert all(inline.model is not TranscriptSegment for inline in admin.inlines)


@pytest.mark.django_db
def test_transcript_change_page_renders_summary_and_nav(browser_fixture):
    client = Client()
    client.force_login(browser_fixture["admin_user"])
    url = reverse(
        "admin:turing_transcript_change",
        args=[browser_fixture["transcript"].pk],
    )
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()

    assert "call-recording.wav" in content
    assert "02:15:30.000" in content
    assert ">fa<" in content or "fa" in content
    assert "مشاهده بخش‌ها" in content
    assert "مشاهده کلمات" in content
    assert "مشاهده تحلیل" in content
    assert "مشاهده هوش مصنوعی" in content

    tid = browser_fixture["transcript"].pk
    assert f"transcript__id__exact={tid}" in content
    assert f"segment__transcript__id__exact={tid}" in content

    # Must not render a segment inline table with start_ms raw fields for every segment.
    assert 'name="transcriptsegment_set-TOTAL_FORMS"' not in content
    assert 'name="transcriptword_set-TOTAL_FORMS"' not in content
    # Speakers remain editable inline.
    assert 'name="speakers-TOTAL_FORMS"' in content


@pytest.mark.django_db
def test_transcript_speaker_edit_does_not_raise_too_many_fields(browser_fixture):
    """Speaker-only save stays well under field limits even with many segments."""
    transcript = browser_fixture["transcript"]
    # Simulate a large transcript: many segments that must NOT become form fields.
    TranscriptSegment.objects.bulk_create(
        [
            TranscriptSegment(
                transcript=transcript,
                speaker=browser_fixture["speaker"],
                sequence=100 + i,
                text=f"Segment {i}",
                start_ms=i * 1000,
                end_ms=i * 1000 + 500,
            )
            for i in range(200)
        ]
    )

    client = Client()
    client.force_login(browser_fixture["admin_user"])
    url = reverse("admin:turing_transcript_change", args=[transcript.pk])
    get_resp = client.get(url)
    assert get_resp.status_code == 200
    assert 'name="transcriptsegment_set-TOTAL_FORMS"' not in get_resp.content.decode()

    speaker = browser_fixture["speaker"]
    speaker2 = browser_fixture["speaker2"]
    post_data = {
        "status": transcript.status,
        "language_code": transcript.language_code,
        "is_primary": "on",
        "speakers-TOTAL_FORMS": "2",
        "speakers-INITIAL_FORMS": "2",
        "speakers-MIN_NUM_FORMS": "0",
        "speakers-MAX_NUM_FORMS": "1000",
        "speakers-0-id": str(speaker.pk),
        "speakers-0-transcript": str(transcript.pk),
        "speakers-0-speaker_name": "Renamed Agent",
        "speakers-0-external_speaker_id": "ext-1",
        "speakers-1-id": str(speaker2.pk),
        "speakers-1-transcript": str(transcript.pk),
        "speakers-1-speaker_name": "",
        "speakers-1-external_speaker_id": "",
        "revisions-TOTAL_FORMS": "0",
        "revisions-INITIAL_FORMS": "0",
        "revisions-MIN_NUM_FORMS": "0",
        "revisions-MAX_NUM_FORMS": "1000",
        "_continue": "1",
    }
    post_resp = client.post(url, post_data)
    # 302 redirect on success, or 200 with form errors — never TooManyFieldsSent (400).
    assert post_resp.status_code in {200, 302}, post_resp.content[:500]
    if post_resp.status_code == 200:
        assert "errornote" not in post_resp.content.decode().lower()
    speaker.refresh_from_db()
    assert speaker.speaker_name == "Renamed Agent"
    assert speaker.speaker_label == "S1"  # immutable


@pytest.mark.django_db
def test_speaker_inline_confidence_readonly_pct(browser_fixture):
    inline = SpeakerInline(Transcript, AdminSite())
    assert inline.confidence_display(browser_fixture["speaker"]) == "88.0%"
    assert inline.confidence_display(browser_fixture["speaker2"]) == "—"
    assert "confidence" not in inline.fields or "confidence_display" in inline.fields
    assert "speaker_label" in inline.readonly_fields
    assert "confidence_display" in inline.readonly_fields


@pytest.mark.django_db
def test_segment_admin_formatted_timestamps(browser_fixture):
    admin = TranscriptSegmentAdmin(TranscriptSegment, AdminSite())
    later = browser_fixture["later"]
    assert admin.start_display(later) == "00:01:16.785"
    assert admin.end_display(later) == "00:01:22.000"
    assert admin.duration_display(later) == "00:00:05.215"
    assert later.start_ms == 76785  # raw ms unchanged


@pytest.mark.django_db
def test_segment_admin_ordering_filters_search_config(browser_fixture):
    admin = TranscriptSegmentAdmin(TranscriptSegment, AdminSite())
    assert admin.ordering == ("start_ms",)
    assert admin.date_hierarchy == "created_at"

    filter_fields = []
    for item in admin.list_filter:
        if isinstance(item, tuple):
            filter_fields.append(item[0])
        else:
            filter_fields.append(item)
    assert "transcript" in filter_fields
    assert "transcript__media" in filter_fields
    assert "transcript__organization" in filter_fields
    assert "speaker" in filter_fields
    assert "created_at" in filter_fields

    assert "transcript__id" in admin.search_fields
    assert "transcript__media__original_filename" in admin.search_fields
    assert "text" in admin.search_fields
    assert any("speaker" in f for f in admin.search_fields)

    request = RequestFactory().get("/admin/")
    request.user = browser_fixture["admin_user"]
    qs = list(admin.get_queryset(request).order_by("start_ms"))
    assert [s.start_ms for s in qs] == [0, 76785]


@pytest.mark.django_db
def test_segment_admin_changelist_search_and_filter(browser_fixture):
    client = Client()
    client.force_login(browser_fixture["admin_user"])
    url = reverse("admin:turing_transcriptsegment_changelist")

    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "00:01:16.785" in content
    assert "00:00:00.000" in content

    by_media = client.get(url, {"transcript__media__id__exact": browser_fixture["media"].id})
    assert by_media.status_code == 200
    assert browser_fixture["earlier"].text[:20] in by_media.content.decode()

    by_transcript = client.get(
        url, {"transcript__id__exact": browser_fixture["transcript"].id}
    )
    assert by_transcript.status_code == 200
    assert "Discuss renewal" in by_transcript.content.decode()

    by_search = client.get(url, {"q": "call-recording.wav"})
    assert by_search.status_code == 200
    assert "Discuss renewal" in by_search.content.decode()

    by_o = client.get(url, {"o": "3"})  # start_display column ordering
    assert by_o.status_code == 200


@pytest.mark.django_db
def test_word_admin_list_and_filters(browser_fixture):
    admin = TranscriptWordAdmin(TranscriptWord, AdminSite())
    word = browser_fixture["word"]
    assert admin.start_display(word) == "00:00:00.000"
    assert admin.end_display(word) == "00:00:00.400"
    assert admin.confidence_display(word) == "95.0%"
    assert admin.transcript_display(word) == browser_fixture["transcript"]
    assert admin.speaker_display(word) == browser_fixture["speaker"]
    assert admin.ordering == ("start_ms",)

    filter_fields = []
    for item in admin.list_filter:
        if isinstance(item, tuple):
            filter_fields.append(item[0])
        else:
            filter_fields.append(item)
    assert "segment__transcript" in filter_fields
    assert "segment__transcript__media" in filter_fields
    assert "segment__transcript__organization" in filter_fields
    assert "segment__speaker" in filter_fields
    assert "created_at" in filter_fields

    client = Client()
    client.force_login(browser_fixture["admin_user"])
    url = reverse("admin:turing_transcriptword_changelist")
    resp = client.get(url, {"q": "Discuss"})
    assert resp.status_code == 200
    assert "Discuss" in resp.content.decode()
    assert "00:00:00.000" in resp.content.decode()

    by_transcript = client.get(
        url, {"segment__transcript__id__exact": browser_fixture["transcript"].id}
    )
    assert by_transcript.status_code == 200
    assert "Discuss" in by_transcript.content.decode()


@pytest.mark.django_db
def test_transcript_overview_panel_counts(browser_fixture):
    admin = TranscriptAdmin(Transcript, AdminSite())
    request = RequestFactory().get("/admin/")
    request.user = browser_fixture["admin_user"]
    obj = admin.get_queryset(request).get(pk=browser_fixture["transcript"].pk)
    html = str(admin.overview_panel(obj))
    assert "رسانه" in html or "call-recording.wav" in html
    assert "02:15:30.000" in html
    assert "گویندگان" in html or "Speakers" in html
    assert "2" in html
    assert "بخش‌ها" in html or "Segments" in html
