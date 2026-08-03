"""Admin logout flow — no blank GET page; redirect to login after POST."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from turing.admin.persian import configure_admin_site

User = get_user_model()

# Ensure logout patch is applied (AppConfig.ready also calls this).
configure_admin_site()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser("logout-admin", "logout@example.com", "pass")


@pytest.mark.django_db
def test_admin_logout_get_shows_confirmation_not_blank(client, admin_user):
    client.force_login(admin_user)
    resp = client.get(reverse("admin:logout"))
    assert resp.status_code == 200
    assert len(resp.content) > 0
    body = resp.content.decode()
    assert "Log out" in body or "خروج" in body
    assert 'method="post"' in body.lower()
    assert reverse("admin:logout") in body
    # Still authenticated until POST
    assert "_auth_user_id" in client.session


@pytest.mark.django_db
def test_admin_logout_post_redirects_to_login_and_blocks_admin(client, admin_user):
    client.force_login(admin_user)
    resp = client.post(reverse("admin:logout"))
    assert resp.status_code == 302
    assert reverse("admin:login") in resp.url

    assert "_auth_user_id" not in client.session

    denied = client.get(reverse("admin:index"))
    assert denied.status_code == 302
    assert reverse("admin:login") in denied.url


@pytest.mark.django_db
def test_admin_logout_get_when_anonymous_redirects_to_login(client):
    # AdminSite.admin_view sends anonymous /logout/ hits to the index first;
    # following redirects lands on the login page (no blank 405 body).
    resp = client.get(reverse("admin:logout"), follow=True)
    assert resp.status_code == 200
    final_path = resp.request["PATH_INFO"]
    assert final_path.rstrip("/").endswith("login") or reverse("admin:login") in {
        url for url, _status in resp.redirect_chain
    }
    assert len(resp.content) > 0


@pytest.mark.django_db
def test_speech_center_uses_post_logout_form(client, admin_user):
    client.force_login(admin_user)
    resp = client.get(reverse("speech_center:dashboard"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'action="' + reverse("admin:logout") + '"' in body or "admin/logout" in body
    assert "sc-logout-form" in body
    assert 'method="post"' in body.lower()
