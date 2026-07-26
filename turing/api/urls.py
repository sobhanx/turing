from __future__ import annotations

from rest_framework.routers import DefaultRouter

from turing.api.viewsets import (
    MediaAssetViewSet,
    ProcessingJobViewSet,
    ProviderViewSet,
    SpeakerViewSet,
    TranscriptSegmentViewSet,
    TranscriptViewSet,
)

router = DefaultRouter()
router.register(r"v1/media", MediaAssetViewSet, basename="turing-media")
router.register(r"v1/jobs", ProcessingJobViewSet, basename="turing-jobs")
router.register(r"v1/transcripts", TranscriptViewSet, basename="turing-transcripts")
router.register(r"v1/segments", TranscriptSegmentViewSet, basename="turing-segments")
router.register(r"v1/speakers", SpeakerViewSet, basename="turing-speakers")
router.register(r"v1/providers", ProviderViewSet, basename="turing-providers")

urlpatterns = router.urls
