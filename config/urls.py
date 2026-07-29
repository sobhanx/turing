from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("speech-center/", include("turing.ui.speech_center.urls")),
    path("api/turing/", include("turing.api.urls")),
]
