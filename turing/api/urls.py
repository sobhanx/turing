from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from turing.api.viewsets import (
    ConnectorCatalogViewSet,
    ConnectorInstallationViewSet,
    ConnectorSyncJobViewSet,
    ExternalReferenceViewSet,
    MediaAssetViewSet,
    ProcessingJobViewSet,
    ProviderViewSet,
    SpeakerViewSet,
    TranscriptAnalysisViewSet,
    TranscriptSegmentViewSet,
    TranscriptViewSet,
    WebhookSubscriptionViewSet,
)
from turing.api.webhooks import speechmatics_webhook

router = DefaultRouter()
router.register(r"v1/media", MediaAssetViewSet, basename="turing-media")
router.register(r"v1/jobs", ProcessingJobViewSet, basename="turing-jobs")
router.register(r"v1/transcripts", TranscriptViewSet, basename="turing-transcripts")
router.register(r"v1/segments", TranscriptSegmentViewSet, basename="turing-segments")
router.register(r"v1/speakers", SpeakerViewSet, basename="turing-speakers")
router.register(r"v1/analyses", TranscriptAnalysisViewSet, basename="turing-analyses")
router.register(
    r"v1/external-references",
    ExternalReferenceViewSet,
    basename="turing-external-references",
)
router.register(r"v1/providers", ProviderViewSet, basename="turing-providers")
router.register(r"v1/webhooks", WebhookSubscriptionViewSet, basename="turing-webhooks")
router.register(
    r"v1/connectors",
    ConnectorCatalogViewSet,
    basename="turing-connectors",
)
router.register(
    r"v1/connector-installations",
    ConnectorInstallationViewSet,
    basename="turing-connector-installations",
)
router.register(
    r"v1/connector-sync-jobs",
    ConnectorSyncJobViewSet,
    basename="turing-connector-sync-jobs",
)

urlpatterns = [
    # Inbound provider callback — must stay before the webhooks detail route.
    path("v1/webhooks/speechmatics/", speechmatics_webhook, name="turing-webhook-speechmatics"),
    *router.urls,
]
