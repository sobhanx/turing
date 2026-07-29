from __future__ import annotations

from django.urls import path

from turing.ui.speech_center import views

app_name = "speech_center"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("create/", views.create_transcript, name="create_transcript"),
    path("queue/", views.queue, name="queue"),
    path("queue/<uuid:job_id>/retry/", views.retry_job, name="retry_job"),
    path("transcripts/", views.transcripts, name="transcripts"),
    path("transcripts/<uuid:transcript_id>/", views.transcript_detail, name="transcript_detail"),
]
