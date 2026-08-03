from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from turing.ui.speech_center.i18n import set_language

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/setlang/", set_language, name="set_language"),
    path("speech-center/", include("turing.ui.speech_center.urls")),
    path("api/turing/", include("turing.api.urls")),
]
