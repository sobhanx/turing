from __future__ import annotations

from django.urls import path

from turing.ui.speech_center import views

app_name = "speech_center"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload_media, name="upload_media"),
    path("create/", views.create_transcript, name="create_transcript"),
    path("queue/", views.queue, name="queue"),
    path("queue/<uuid:job_id>/retry/", views.retry_job, name="retry_job"),
    path("queue/<uuid:job_id>/cancel/", views.cancel_job, name="cancel_job"),
    path("meetings/", views.meetings, name="meetings"),
    path("transcripts/", views.transcripts, name="transcripts"),
    path("transcripts/<uuid:transcript_id>/", views.transcript_detail, name="transcript_detail"),
    path(
        "transcripts/<uuid:transcript_id>/segments/",
        views.transcript_segments,
        name="transcript_segments",
    ),
    path(
        "transcripts/<uuid:transcript_id>/generate-ai-insights/",
        views.generate_ai_insights,
        name="generate_ai_insights",
    ),
    path(
        "transcripts/<uuid:transcript_id>/export/<str:fmt>/",
        views.export_transcript,
        name="export_transcript",
    ),
]
