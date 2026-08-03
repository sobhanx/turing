from __future__ import annotations

"""Speech Center internationalization (Django i18n) tests."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import translation

User = get_user_model()


@pytest.fixture
def sc_user(db):
    return User.objects.create_superuser("sc-i18n", "sc-i18n@example.com", "pass")


@pytest.fixture
def sc_client(client, sc_user):
    client.force_login(sc_user)
    return client


@pytest.mark.django_db
def test_default_language_is_english(sc_client):
    resp = sc_client.get(reverse("speech_center:dashboard"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'lang="en"' in body
    assert 'dir="ltr"' in body
    assert "Quick Actions" in body
    assert "Hello," in body


@pytest.mark.django_db
def test_set_language_to_persian_persists_in_session(sc_client):
    dashboard = reverse("speech_center:dashboard")
    resp = sc_client.post(
        reverse("set_language"),
        {"language": "fa", "next": dashboard},
    )
    assert resp.status_code == 302
    assert sc_client.session.get("django_language") == "fa"

    page = sc_client.get(dashboard)
    assert page.status_code == 200
    body = page.content.decode()
    assert 'lang="fa"' in body
    assert 'dir="rtl"' in body
    assert "مرکز گفتار" in body
    assert "اقدامات سریع" in body
    assert "sc-rtl" in body
    assert 'class="sc-lang-btn is-active"' in body
    assert "فارسی" in body


@pytest.mark.django_db
def test_switch_back_to_english(sc_client):
    queue = reverse("speech_center:queue")
    sc_client.post(reverse("set_language"), {"language": "fa", "next": queue})
    assert sc_client.session.get("django_language") == "fa"

    resp = sc_client.post(
        reverse("set_language"),
        {"language": "en", "next": queue},
    )
    assert resp.status_code == 302
    assert sc_client.session.get("django_language") == "en"

    page = sc_client.get(queue)
    body = page.content.decode()
    assert 'lang="en"' in body
    assert 'dir="ltr"' in body
    assert "Processing Queue" in body
    assert "sc-rtl" not in body


@pytest.mark.django_db
def test_language_switch_reloads_current_page(sc_client):
    queue = reverse("speech_center:queue")
    resp = sc_client.post(
        reverse("set_language"),
        {"language": "fa", "next": queue},
    )
    assert resp.status_code == 302
    assert resp["Location"].endswith(queue) or queue in resp["Location"]


@pytest.mark.django_db
def test_key_pages_render_translated_strings(sc_client):
    sc_client.post(
        reverse("set_language"),
        {"language": "fa", "next": reverse("speech_center:dashboard")},
    )
    pages = {
        reverse("speech_center:dashboard"): ("اقدامات سریع", "فعالیت اخیر"),
        reverse("speech_center:upload_media"): ("بارگذاری محتوا", "ضبط صدا"),
        reverse("speech_center:create_transcript"): ("ایجاد رونوشت",),
        reverse("speech_center:queue"): ("صف پردازش",),
        reverse("speech_center:transcripts"): ("رونوشت‌ها",),
    }
    for url, needles in pages.items():
        resp = sc_client.get(url)
        assert resp.status_code == 200, url
        body = resp.content.decode()
        assert 'dir="rtl"' in body
        for needle in needles:
            assert needle in body, f"{needle!r} missing on {url}"


@pytest.mark.django_db
def test_presentation_labels_follow_active_language():
    from turing.domain.enums import JobStatus
    from turing.models import ProcessingJob
    from turing.ui.speech_center.presentation import job_display_status

    job = ProcessingJob(status=JobStatus.QUEUED, ingest_status="pending", attempt_count=0)
    with translation.override("en"):
        assert job_display_status(job)[0] == "Queued"
    with translation.override("fa"):
        assert job_display_status(job)[0] == "در صف"
