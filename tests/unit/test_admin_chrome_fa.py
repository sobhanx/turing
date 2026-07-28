from __future__ import annotations

"""Persian Admin chrome i18n overrides (presentation only)."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _

User = get_user_model()


@pytest.mark.parametrize(
    ("msgid", "expected"),
    [
        ("Home", "خانه"),
        ("Add", "افزودن"),
        ("Change", "مدیریت"),
        ("View", "مشاهده"),
        ("Actions", "عملیات"),
        ("Search", "جستجو"),
        ("Go", "اجرا"),
        ("Username", "نام کاربری"),
        ("Password", "رمز عبور"),
        ("Log in", "ورود"),
        ("Change password", "تغییر رمز عبور"),
        ("View site", "مشاهده سایت"),
        ("Model name", "مدل"),
        ("Add link", "افزودن"),
        ("Change or view list link", "مدیریت"),
        ("Save", "ذخیره"),
        ("Save and continue editing", "ذخیره و ادامه ویرایش"),
        ("Save and add another", "ذخیره و افزودن مورد جدید"),
        ("%(app)s administration", "مدیریت %(app)s"),
        ("Are you sure?", "آیا مطمئن هستید؟"),
        ("Toggle theme (current theme: auto)", "تغییر پوسته (پوسته فعلی: خودکار)"),
    ],
)
def test_admin_chrome_persian_overrides(msgid, expected):
    with translation.override("fa"):
        assert _(msgid) == expected


@pytest.mark.django_db
def test_admin_index_and_login_are_persian():
    client = Client()
    login_url = reverse("admin:login")
    resp = client.get(login_url)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "ورود" in body
    assert "نام کاربری" in body or "username" in body.lower()
    assert "رمز عبور" in body or "password" in body.lower()

    user = User.objects.create_superuser("fa-admin", "fa@example.com", "pass")
    client.force_login(user)
    index = client.get(reverse("admin:index"))
    assert index.status_code == 200
    content = index.content.decode()
    assert "خانه" in content or "داشبورد" in content or "پنل عملیات" in content
    assert "افزودن" in content
    assert "مدیریت" in content
    assert "مدل" in content
    # English chrome should not dominate
    assert "Change password" not in content
    assert "Log out" not in content
