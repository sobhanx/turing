from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from turing.api.viewsets import (
    MediaAssetViewSet,
    ProcessingJobViewSet,
    ProviderViewSet,
    SpeakerViewSet,
    TranscriptSegmentViewSet,
    TranscriptViewSet,
)
from turing.api.webhooks import speechmatics_webhook

router = DefaultRouter()
router.register(r"v1/media", MediaAssetViewSet, basename="turing-media")
router.register(r"v1/jobs", ProcessingJobViewSet, basename="turing-jobs")
router.register(r"v1/transcripts", TranscriptViewSet, basename="turing-transcripts")
router.register(r"v1/segments", TranscriptSegmentViewSet, basename="turing-segments")
router.register(r"v1/speakers", SpeakerViewSet, basename="turing-speakers")
router.register(r"v1/providers", ProviderViewSet, basename="turing-providers")

urlpatterns = [
    path("v1/webhooks/speechmatics/", speechmatics_webhook, name="turing-webhook-speechmatics"),
    *router.urls,
]
