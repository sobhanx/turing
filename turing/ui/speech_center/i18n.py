"""Speech Center language switching helpers (Django i18n)."""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import check_for_language
from django.views.i18n import set_language as django_set_language

# Session key used alongside Django's language cookie (Django 5+ is cookie-only).
LANGUAGE_SESSION_KEY = "django_language"


def set_language(request):
    """
    Django ``set_language`` plus session persistence.

    Django 5+ stores the active language in a cookie only. Speech Center also
    writes ``request.session['django_language']`` so the choice survives when
    the cookie is missing and tests can assert session persistence.
    """
    response = django_set_language(request)
    if request.method == "POST" and hasattr(request, "session"):
        lang_code = request.POST.get("language")
        if lang_code and check_for_language(lang_code):
            request.session[LANGUAGE_SESSION_KEY] = lang_code
    return response


class SessionLanguageMiddleware:
    """
    Bridge session language into LocaleMiddleware (cookie-based) resolution.

    Order: SessionMiddleware → SessionLanguageMiddleware → LocaleMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "session"):
            cookie_name = settings.LANGUAGE_COOKIE_NAME
            session_lang = request.session.get(LANGUAGE_SESSION_KEY)
            cookie_lang = request.COOKIES.get(cookie_name)
            if session_lang and check_for_language(session_lang):
                mutable = request.COOKIES.copy()
                mutable[cookie_name] = session_lang
                request.COOKIES = mutable
            elif cookie_lang and check_for_language(cookie_lang):
                request.session[LANGUAGE_SESSION_KEY] = cookie_lang
        return self.get_response(request)
