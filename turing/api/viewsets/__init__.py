from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from turing.api.filters import (
    ConnectorInstallationFilter,
    MediaAssetFilter,
    ProcessingJobFilter,
    TranscriptAnalysisFilter,
    TranscriptFilter,
)
from turing.api.serializers import (
    ConnectorInstallationSerializer,
    ConnectorInstallationUpdateSerializer,
    ConnectorInstallationWriteSerializer,
    ConnectorSyncJobSerializer,
    CreateTranscriptionJobSerializer,
    ExternalReferenceCreateSerializer,
    ExternalReferenceSerializer,
    MediaAssetSerializer,
    MediaUploadSerializer,
    ProcessingJobSerializer,
    ProcessingLogSerializer,
    SegmentUpdateSerializer,
    SpeakerRenameSerializer,
    SpeakerSerializer,
    SpeechCenterAnalysisSerializer,
    SpeechCenterMediaSerializer,
    SpeechCenterTimelineAnalysisRefSerializer,
    SpeechCenterTranscriptSerializer,
    TranscriptAnalysisSerializer,
    TranscriptListSerializer,
    TranscriptRevisionSerializer,
    TranscriptSegmentSerializer,
    TranscriptSerializer,
    WebhookDeliverySerializer,
    WebhookSubscriptionSerializer,
    WebhookSubscriptionUpdateSerializer,
    WebhookSubscriptionWriteSerializer,
)
from turing.auth.permissions import (
    CanApproveTranscript,
    CanEditTranscript,
    CanManageConfig,
    CanManageJobs,
    CanUploadMedia,
    HasTuringCapability,
)
from turing.auth.tenancy import resolve_organization, scope_by_organization
from turing.domain.exceptions import PermissionDeniedError, TuringError
from turing.models import (
    ConnectorInstallation,
    ConnectorSyncJob,
    ExternalReference,
    MediaAsset,
    ProcessingJob,
    Speaker,
    Transcript,
    TranscriptAnalysis,
    TranscriptSegment,
    WebhookDelivery,
    WebhookSubscription,
)
from turing.services.external_reference import ExternalReferenceService
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService
from turing.services.transcript_analysis import TranscriptAnalysisService


def _error_response(exc: TuringError):
    status_code = (
        status.HTTP_403_FORBIDDEN
        if isinstance(exc, PermissionDeniedError)
        else status.HTTP_400_BAD_REQUEST
    )
    if getattr(exc, "code", None) == "not_found":
        status_code = status.HTTP_404_NOT_FOUND
    return Response({"detail": exc.message, "code": exc.code}, status=status_code)


class MediaAssetViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MediaAsset.objects.all().select_related("uploaded_by", "organization")
    serializer_class = MediaAssetSerializer
    filterset_class = MediaAssetFilter
    search_fields = ("original_filename", "external_url", "checksum", "tenant_key")
    ordering_fields = ("created_at", "byte_size")
    required_capability = "upload_media"
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]

    def get_queryset(self):
        qs = scope_by_organization(super().get_queryset(), self.request.user)
        if any(
            key in self.request.query_params
            for key in ("external_system", "external_type", "external_id")
        ):
            qs = qs.distinct()
        return qs.prefetch_related("external_references")

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), CanUploadMedia()]
        if self.action == "external_references" and self.request.method == "POST":
            return [IsAuthenticated(), CanUploadMedia()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        serializer = MediaUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        service = MediaService()
        try:
            if data.get("file"):
                uploaded = data["file"]
                asset = service.create_from_upload(
                    uploaded_file=uploaded,
                    filename=uploaded.name,
                    content_type=getattr(uploaded, "content_type", "") or "",
                    use_case=data.get("use_case"),
                    uploaded_by=request.user,
                    tenant_key=data.get("tenant_key") or "",
                    organization_id=data.get("organization_id"),
                    metadata=data.get("metadata") or {},
                )
            else:
                asset = service.create_from_url(
                    url=data["external_url"],
                    use_case=data.get("use_case"),
                    uploaded_by=request.user,
                    tenant_key=data.get("tenant_key") or "",
                    organization_id=data.get("organization_id"),
                    metadata=data.get("metadata") or {},
                )
            for ref_data in data.get("external_references") or []:
                ExternalReferenceService().attach_to_media(
                    asset,
                    external_system=ref_data["external_system"],
                    external_type=ref_data["external_type"],
                    external_id=ref_data["external_id"],
                    user=request.user,
                    metadata=ref_data.get("metadata"),
                )
        except TuringError as exc:
            return _error_response(exc)
        asset = MediaAsset.objects.prefetch_related("external_references").get(pk=asset.pk)
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="external-references")
    def external_references(self, request, pk=None):
        media = self.get_object()
        service = ExternalReferenceService()
        if request.method == "GET":
            refs = service.list_for_media(media, user=request.user)
            page = self.paginate_queryset(refs)
            if page is not None:
                return self.get_paginated_response(
                    ExternalReferenceSerializer(page, many=True).data
                )
            return Response(ExternalReferenceSerializer(refs, many=True).data)

        serializer = ExternalReferenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            ref, created = service.attach_to_media(
                media,
                external_system=data["external_system"],
                external_type=data["external_type"],
                external_id=data["external_id"],
                user=request.user,
                metadata=data.get("metadata"),
            )
        except TuringError as exc:
            return _error_response(exc)
        return Response(
            ExternalReferenceSerializer(ref).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ProcessingJobViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = ProcessingJob.objects.all().select_related("media", "created_by", "organization")
    serializer_class = ProcessingJobSerializer
    filterset_class = ProcessingJobFilter
    search_fields = ("id", "external_job_id", "idempotency_key", "error_code")
    ordering_fields = ("created_at", "priority", "finished_at")
    required_capability = "manage_jobs"
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]

    def get_queryset(self):
        return scope_by_organization(super().get_queryset(), self.request.user)

    def get_permissions(self):
        if self.action in {"create", "retry", "cancel"}:
            return [IsAuthenticated(), CanManageJobs()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        serializer = CreateTranscriptionJobSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        media_qs = scope_by_organization(
            MediaAsset.objects.all(), request.user
        )
        media = get_object_or_404(media_qs, pk=data["media_id"])
        idem = data.get("idempotency_key") or request.headers.get("Idempotency-Key", "")
        orch = JobOrchestrator()
        try:
            job = orch.create_transcription_job(
                media=media,
                provider_code=data.get("provider_code") or None,
                language_code=data.get("language_code") or "",
                options=data.get("options"),
                created_by=request.user,
                idempotency_key=idem or "",
                priority=data.get("priority", 100),
                auto_enqueue=data.get("auto_enqueue", True),
            )
        except TuringError as exc:
            return _error_response(exc)
        return Response(ProcessingJobSerializer(job).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        orch = JobOrchestrator()
        try:
            job = orch.retry(self.get_object())
        except TuringError as exc:
            return _error_response(exc)
        return Response(ProcessingJobSerializer(job).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        orch = JobOrchestrator()
        try:
            job = orch.cancel(self.get_object())
        except TuringError as exc:
            return _error_response(exc)
        return Response(ProcessingJobSerializer(job).data)

    @action(detail=True, methods=["get"])
    def logs(self, request, pk=None):
        job = self.get_object()
        logs = job.logs.all()[:200]
        return Response(ProcessingLogSerializer(logs, many=True).data)


class TranscriptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Transcript.objects.all().select_related("media", "job", "organization")
    filterset_class = TranscriptFilter
    search_fields = ("full_text", "id", "language_code")
    ordering_fields = ("created_at", "updated_at", "version")
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]

    def get_serializer_class(self):
        if self.action == "list":
            return TranscriptListSerializer
        return TranscriptSerializer

    def get_queryset(self):
        qs = scope_by_organization(super().get_queryset(), self.request.user)
        if any(
            key in self.request.query_params
            for key in ("external_system", "external_type", "external_id")
        ):
            qs = qs.distinct()
        if self.action == "retrieve":
            return qs.prefetch_related(
                "speakers",
                "segments__speaker",
                "external_references",
            )
        return qs.prefetch_related("external_references")

    def get_permissions(self):
        if self.action == "submit_review":
            # Editors (and reviewers) may submit; only reviewers/admins approve.
            return [IsAuthenticated(), CanEditTranscript()]
        if self.action == "approve":
            return [IsAuthenticated(), CanApproveTranscript()]
        if self.action == "external_references" and self.request.method == "POST":
            return [IsAuthenticated(), CanEditTranscript()]
        return super().get_permissions()

    @action(detail=True, methods=["get"])
    def revisions(self, request, pk=None):
        transcript = self.get_object()
        return Response(
            TranscriptRevisionSerializer(transcript.revisions.all(), many=True).data
        )

    @action(detail=True, methods=["get", "post"], url_path="external-references")
    def external_references(self, request, pk=None):
        transcript = self.get_object()
        service = ExternalReferenceService()
        if request.method == "GET":
            refs = service.list_for_transcript(transcript, user=request.user)
            page = self.paginate_queryset(refs)
            if page is not None:
                return self.get_paginated_response(
                    ExternalReferenceSerializer(page, many=True).data
                )
            return Response(ExternalReferenceSerializer(refs, many=True).data)

        serializer = ExternalReferenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            ref, created = service.attach_to_transcript(
                transcript,
                external_system=data["external_system"],
                external_type=data["external_type"],
                external_id=data["external_id"],
                user=request.user,
                metadata=data.get("metadata"),
            )
        except TuringError as exc:
            return _error_response(exc)
        return Response(
            ExternalReferenceSerializer(ref).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def analyses(self, request, pk=None):
        """List derived AI analyses for a transcript (newest first)."""
        transcript = self.get_object()
        analysis_type = request.query_params.get("analysis_type") or None
        queryset = TranscriptAnalysisService().list_for_transcript(
            transcript,
            user=request.user,
            analysis_type=analysis_type,
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(
                TranscriptAnalysisSerializer(page, many=True).data
            )
        return Response(TranscriptAnalysisSerializer(queryset, many=True).data)

    @action(detail=True, methods=["get"], url_path="analyses/latest")
    def analyses_latest(self, request, pk=None):
        """Return the newest analysis row for ``?type=`` (append-only history)."""
        transcript = self.get_object()
        analysis_type = request.query_params.get("type") or request.query_params.get(
            "analysis_type"
        )
        try:
            analysis = TranscriptAnalysisService().latest_by_type(
                transcript,
                analysis_type=analysis_type or "",
                user=request.user,
            )
        except TuringError as exc:
            return _error_response(exc)
        return Response(TranscriptAnalysisSerializer(analysis).data)

    @action(detail=True, methods=["post"])
    def submit_review(self, request, pk=None):
        transcript = self.get_object()
        assignee_id = request.data.get("assignee_id") or request.user.id
        from django.contrib.auth import get_user_model

        User = get_user_model()
        assignee = get_object_or_404(User, pk=assignee_id)
        try:
            assignment = TranscriptService().submit_for_review(
                transcript=transcript,
                assignee=assignee,
                assigned_by=request.user,
                notes=request.data.get("notes", ""),
            )
        except TuringError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=400)
        return Response(
            {
                "id": str(assignment.id),
                "status": assignment.status,
                "transcript": str(transcript.id),
                "transcript_status": TranscriptService().get(transcript.id).status,
            }
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        transcript = self.get_object()
        try:
            updated = TranscriptService().approve(
                transcript=transcript,
                approved_by=request.user,
            )
        except TuringError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=400)
        return Response(TranscriptSerializer(updated).data)


class TranscriptSegmentViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = TranscriptSegment.objects.select_related("transcript", "speaker", "transcript__organization")
    serializer_class = TranscriptSegmentSerializer
    http_method_names = ["get", "patch", "head", "options"]
    permission_classes = [IsAuthenticated, HasTuringCapability]
    read_capability = "view_transcript"

    def get_queryset(self):
        return scope_by_organization(
            super().get_queryset(),
            self.request.user,
            field="transcript__organization_id",
        )

    def get_permissions(self):
        if self.action in {"partial_update", "update"}:
            return [IsAuthenticated(), CanEditTranscript()]
        return super().get_permissions()

    def partial_update(self, request, *args, **kwargs):
        segment = self.get_object()
        serializer = SegmentUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        speaker = None
        if "speaker_id" in data:
            speaker = get_object_or_404(
                Speaker, pk=data["speaker_id"], transcript=segment.transcript
            )
        try:
            updated = TranscriptService().update_segment(
                segment=segment,
                text=data.get("text"),
                speaker=speaker,
                start_ms=data.get("start_ms"),
                end_ms=data.get("end_ms"),
                edited_by=request.user,
            )
        except TuringError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=400)
        return Response(TranscriptSegmentSerializer(updated).data)


class SpeakerViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Speaker.objects.select_related("transcript", "transcript__organization")
    serializer_class = SpeakerSerializer
    http_method_names = ["get", "patch", "head", "options"]
    permission_classes = [IsAuthenticated, HasTuringCapability]
    read_capability = "view_transcript"

    def get_queryset(self):
        return scope_by_organization(
            super().get_queryset(),
            self.request.user,
            field="transcript__organization_id",
        )

    def get_permissions(self):
        if self.action in {"partial_update", "update"}:
            return [IsAuthenticated(), CanEditTranscript()]
        return super().get_permissions()

    def partial_update(self, request, *args, **kwargs):
        speaker = self.get_object()
        serializer = SpeakerRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            updated = TranscriptService().rename_speaker(
                speaker=speaker,
                display_name=serializer.validated_data["display_name"],
                edited_by=request.user,
            )
        except TuringError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=400)
        return Response(SpeakerSerializer(updated).data)


class TranscriptAnalysisViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only access to append-only transcript intelligence rows."""

    queryset = TranscriptAnalysis.objects.all().select_related(
        "transcript",
        "organization",
    )
    serializer_class = TranscriptAnalysisSerializer
    filterset_class = TranscriptAnalysisFilter
    search_fields = ("id", "provider", "model_name")
    ordering_fields = ("created_at", "analysis_type")
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        return TranscriptAnalysisService().scope_queryset(
            super().get_queryset(),
            self.request.user,
        )


class ExternalReferenceViewSet(
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Retrieve/delete host object links (create via media/transcript nested routes)."""

    queryset = ExternalReference.objects.all().select_related(
        "organization",
        "media",
        "transcript",
    )
    serializer_class = ExternalReferenceSerializer
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "delete", "head", "options"]

    def get_queryset(self):
        return ExternalReferenceService().scope_queryset(
            super().get_queryset(),
            self.request.user,
        )

    def get_permissions(self):
        if self.action == "destroy":
            # Object-level capability chosen in destroy based on target kind.
            return [IsAuthenticated(), HasTuringCapability()]
        return super().get_permissions()

    def destroy(self, request, *args, **kwargs):
        reference = self.get_object()
        # Enforce write capability for the target type before delete.
        from turing.auth.roles import user_has_capability

        capability = "upload_media" if reference.media_id else "edit_transcript"
        if not user_has_capability(
            request.user,
            capability,
            organization=reference.organization,
        ):
            return Response(
                {"detail": f"Missing capability '{capability}'.", "code": "permission_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            ExternalReferenceService().detach(reference, user=request.user)
        except TuringError as exc:
            return _error_response(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProviderViewSet(viewsets.ViewSet):
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]

    def list(self, request):
        from turing.models import SpeechProviderConfig
        from turing.providers.registry import ProviderRegistry

        registered = set(ProviderRegistry.codes())
        rows = SpeechProviderConfig.objects.filter(is_active=True)
        data = [
            {
                "code": row.code,
                "name": row.name,
                "registered": row.code in registered,
                "priority": row.priority,
            }
            for row in rows
        ]
        # Include registered providers without DB row
        for code in registered:
            if not any(d["code"] == code for d in data):
                data.append(
                    {
                        "code": code,
                        "name": code.title(),
                        "registered": True,
                        "priority": 100,
                    }
                )
        return Response(data)


class WebhookSubscriptionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Org-scoped outbound webhook subscription CRUD (Phase 4.2.4).

    Signing secrets are generated on create and returned once; never in serializers.
    """

    queryset = WebhookSubscription.objects.all().select_related("organization")
    serializer_class = WebhookSubscriptionSerializer
    required_capability = "manage_config"
    read_capability = "manage_config"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    search_fields = ("name", "url")
    ordering_fields = ("created_at", "name", "updated_at")

    def get_queryset(self):
        return scope_by_organization(super().get_queryset(), self.request.user)

    def get_permissions(self):
        if self.action in {"create", "partial_update", "update", "destroy"}:
            return [IsAuthenticated(), CanManageConfig()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        import secrets

        serializer = WebhookSubscriptionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            organization = resolve_organization(
                organization_id=data.get("organization_id"),
                user=request.user,
                capability="manage_config",
            )
        except TuringError as exc:
            return _error_response(exc)

        signing_secret = secrets.token_urlsafe(32)
        subscription = WebhookSubscription(
            organization=organization,
            name=data["name"],
            url=data["url"],
            secret=signing_secret,
            subscribed_events=data["subscribed_events"],
            is_active=data.get("is_active", True),
        )
        subscription.full_clean()
        subscription.save()

        return Response(
            {
                "subscription": WebhookSubscriptionSerializer(subscription).data,
                "signing_secret": signing_secret,
            },
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        subscription = self.get_object()
        serializer = WebhookSubscriptionUpdateSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "name" in data:
            subscription.name = data["name"]
        if "url" in data:
            subscription.url = data["url"]
        if "subscribed_events" in data:
            subscription.subscribed_events = data["subscribed_events"]
        if "is_active" in data:
            subscription.is_active = data["is_active"]
        subscription.full_clean()
        subscription.save()
        return Response(WebhookSubscriptionSerializer(subscription).data)

    def destroy(self, request, *args, **kwargs):
        subscription = self.get_object()
        subscription.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="deliveries")
    def deliveries(self, request, pk=None):
        subscription = self.get_object()
        qs = (
            WebhookDelivery.objects.filter(subscription=subscription)
            .select_related("outbox_event")
            .order_by("-created_at")
        )
        page = self.paginate_queryset(qs)
        serializer = WebhookDeliverySerializer(page or qs, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


def _validate_connector_config(*, connector_type: str, config: dict, name: str = "tmp"):
    """Resolve registry connector and run validate_config against a temporary installation."""
    from turing.connectors.exceptions import ConnectorError, ConnectorNotFoundError
    from turing.connectors.registry import ConnectorRegistry
    from turing.domain.enums import ConnectorInstallationStatus

    try:
        connector_cls = ConnectorRegistry.get(connector_type)
    except ConnectorNotFoundError as exc:
        raise TuringError(str(exc), code="connector_not_found") from exc

    try:
        ConnectorRegistry.validate_installation_requirements(
            connector_type,
            config=config,
        )
    except ConnectorError as exc:
        raise TuringError(str(exc), code=getattr(exc, "code", "connector_error")) from exc

    temp = ConnectorInstallation(
        connector_type=connector_type,
        name=name or "tmp",
        status=ConnectorInstallationStatus.ACTIVE,
        config=dict(config or {}),
    )
    # organization not required for validate_config on typical connectors
    connector = connector_cls(temp)
    try:
        connector.validate_config()
    except ConnectorError as exc:
        raise TuringError(str(exc), code=getattr(exc, "code", "connector_error")) from exc


class ConnectorCatalogViewSet(viewsets.ViewSet):
    """Discover registered connector types (Phase 4.4.2 marketplace catalog)."""

    required_capability = "manage_config"
    read_capability = "manage_config"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "head", "options"]

    def list(self, request):
        from turing.connectors.registry import ConnectorRegistry

        return Response(ConnectorRegistry.list_available())


class ConnectorInstallationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Org-scoped connector installation CRUD + lifecycle actions."""

    queryset = ConnectorInstallation.objects.all().select_related("organization")
    serializer_class = ConnectorInstallationSerializer
    filterset_class = ConnectorInstallationFilter
    required_capability = "manage_config"
    read_capability = "manage_config"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    search_fields = ("name", "connector_type")
    ordering_fields = ("created_at", "name", "updated_at")

    def get_queryset(self):
        return scope_by_organization(
            super().get_queryset(), self.request.user
        ).prefetch_related("sync_jobs")

    def get_permissions(self):
        if self.action in {
            "create",
            "partial_update",
            "update",
            "destroy",
            "sync",
            "authorize",
            "activate",
            "revoke",
        }:
            return [IsAuthenticated(), CanManageConfig()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        from django.db import IntegrityError

        from turing.connectors.registry import ConnectorRegistry
        from turing.domain.enums import ConnectorAuthType, ConnectorInstallationStatus

        serializer = ConnectorInstallationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            organization = resolve_organization(
                organization_id=data.get("organization_id"),
                user=request.user,
                capability="manage_config",
            )
            _validate_connector_config(
                connector_type=data["connector_type"],
                config=data.get("config") or {},
                name=data["name"],
            )
        except TuringError as exc:
            return _error_response(exc)

        try:
            connector_cls = ConnectorRegistry.get(data["connector_type"].strip())
            auth_type = getattr(connector_cls, "auth_type", ConnectorAuthType.API_KEY)
        except Exception:  # noqa: BLE001
            auth_type = ConnectorAuthType.API_KEY

        if data.get("status"):
            initial_status = data["status"]
        elif auth_type == ConnectorAuthType.OAUTH2:
            initial_status = ConnectorInstallationStatus.PENDING
        else:
            initial_status = ConnectorInstallationStatus.ACTIVE

        installation = ConnectorInstallation(
            organization=organization,
            connector_type=data["connector_type"].strip(),
            name=data["name"].strip(),
            status=initial_status,
            config=dict(data.get("config") or {}),
        )
        try:
            installation.full_clean()
            installation.save()
        except IntegrityError:
            return Response(
                {
                    "detail": "A connector installation with this name already exists.",
                    "code": "validation_error",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # noqa: BLE001
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                return Response(
                    {"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        return Response(
            ConnectorInstallationSerializer(installation).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        from django.db import IntegrityError

        from turing.domain.enums import ConnectorInstallationStatus
        from turing.services.connector_installation import ConnectorInstallationService

        installation = self.get_object()
        serializer = ConnectorInstallationUpdateSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "name" in data:
            installation.name = data["name"].strip()
        if "config" in data:
            installation.config = dict(data["config"] or {})
            try:
                _validate_connector_config(
                    connector_type=installation.connector_type,
                    config=installation.config,
                    name=installation.name,
                )
            except TuringError as exc:
                return _error_response(exc)

        lifecycle = ConnectorInstallationService()
        new_status = data.get("status")
        try:
            if new_status == ConnectorInstallationStatus.REVOKED:
                installation = lifecycle.revoke(installation)
                if "name" in data or "config" in data:
                    if "name" in data:
                        installation.name = data["name"].strip()
                    if "config" in data:
                        installation.config = dict(data["config"] or {})
                    installation.full_clean()
                    installation.save()
            elif new_status == ConnectorInstallationStatus.ACTIVE:
                installation.full_clean()
                installation.save()
                installation = lifecycle.activate(installation)
            elif new_status == ConnectorInstallationStatus.EXPIRED:
                installation.full_clean()
                installation.save()
                installation = lifecycle.expire(installation)
            else:
                if new_status is not None:
                    installation.status = new_status
                installation.full_clean()
                installation.save()
        except TuringError as exc:
            return _error_response(exc)
        except IntegrityError:
            return Response(
                {
                    "detail": "A connector installation with this name already exists.",
                    "code": "validation_error",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        installation.refresh_from_db()
        return Response(ConnectorInstallationSerializer(installation).data)

    def destroy(self, request, *args, **kwargs):
        installation = self.get_object()
        installation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        from turing.services.connector_installation import ConnectorInstallationService

        installation = self.get_object()
        try:
            installation = ConnectorInstallationService().activate(installation)
        except TuringError as exc:
            return _error_response(exc)
        return Response(ConnectorInstallationSerializer(installation).data)

    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request, pk=None):
        from turing.services.connector_installation import ConnectorInstallationService

        installation = self.get_object()
        try:
            installation = ConnectorInstallationService().revoke(installation)
        except TuringError as exc:
            return _error_response(exc)
        installation.refresh_from_db()
        return Response(ConnectorInstallationSerializer(installation).data)

    @action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, pk=None):
        from turing.services.connector_sync import ConnectorSyncService

        installation = self.get_object()
        try:
            job = ConnectorSyncService().start_sync(installation, auto_enqueue=True)
        except TuringError as exc:
            return _error_response(exc)
        return Response(
            {"sync_job_id": str(job.id)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="authorize")
    def authorize(self, request, pk=None):
        """Return the provider OAuth authorization URL for this installation."""
        from turing.connectors.exceptions import ConnectorError
        from turing.connectors.registry import ConnectorRegistry
        from turing.domain.enums import ConnectorAuthType, ConnectorInstallationStatus
        from turing.services.oauth_state import OAuthStateService

        installation = self.get_object()
        try:
            connector = ConnectorRegistry.create(installation)
            if getattr(connector, "auth_type", ConnectorAuthType.API_KEY) != (
                ConnectorAuthType.OAUTH2
            ):
                raise TuringError(
                    "This connector does not support OAuth authorization.",
                    code="oauth_unsupported",
                )
            if installation.status == ConnectorInstallationStatus.REVOKED:
                raise TuringError(
                    "Cannot authorize a revoked connector installation.",
                    code="validation_error",
                )
            state = OAuthStateService().generate(
                installation_id=str(installation.id),
                organization_id=installation.organization_id,
                connector_type=installation.connector_type,
            )
            redirect_uri = request.query_params.get("redirect_uri") or None
            url = connector.authorization_url(
                redirect_uri=redirect_uri,
                state=state,
            )
        except TuringError as exc:
            return _error_response(exc)
        except ConnectorError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": getattr(exc, "code", "connector_error"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "authorization_url": url,
                "installation_id": str(installation.id),
                "connector_type": installation.connector_type,
            }
        )


class ConnectorOAuthCallbackView(APIView):
    """
    OAuth redirect callback (Phase 4.3.6).

    Public GET — ownership is enforced via signed ``state``, not session auth.
    Never returns tokens.
    """

    authentication_classes = []
    permission_classes = []
    http_method_names = ["get", "head", "options"]

    def get(self, request, connector: str):
        from turing.connectors.exceptions import ConnectorError
        from turing.connectors.registry import ConnectorRegistry
        from turing.domain.enums import ConnectorAuthType, ConnectorInstallationStatus
        from turing.services.connector_installation import ConnectorInstallationService
        from turing.services.oauth_state import OAuthStateService

        connector_type = (connector or "").strip()
        code = (request.query_params.get("code") or "").strip()
        state = (request.query_params.get("state") or "").strip()
        error = (request.query_params.get("error") or "").strip()

        if error:
            return Response(
                {
                    "detail": "OAuth authorization was denied or failed.",
                    "code": "oauth_denied",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not code or not state:
            return Response(
                {
                    "detail": "OAuth callback requires code and state.",
                    "code": "validation_error",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            claims = OAuthStateService().validate(
                state,
                expected_connector_type=connector_type,
            )
            installation = ConnectorInstallation.objects.select_related(
                "organization"
            ).get(
                pk=claims.installation_id,
                organization_id=claims.organization_id,
            )
        except ConnectorInstallation.DoesNotExist:
            return Response(
                {"detail": "Connector installation not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except TuringError as exc:
            return _error_response(exc)

        if installation.connector_type != connector_type:
            return Response(
                {
                    "detail": "OAuth connector type does not match installation.",
                    "code": "validation_error",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if installation.status == ConnectorInstallationStatus.REVOKED:
            return Response(
                {
                    "detail": "Cannot authorize a revoked connector installation.",
                    "code": "validation_error",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            connector_obj = ConnectorRegistry.create(installation)
            if getattr(connector_obj, "auth_type", None) != ConnectorAuthType.OAUTH2:
                raise TuringError(
                    "This connector does not support OAuth authorization.",
                    code="oauth_unsupported",
                )
            redirect_uri = request.query_params.get("redirect_uri") or None
            connector_obj.exchange_code(code, redirect_uri=redirect_uri)
            installation = ConnectorInstallationService().activate(installation)
        except TuringError as exc:
            return _error_response(exc)
        except ConnectorError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": getattr(exc, "code", "connector_error"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "installation_id": str(installation.id),
                "connector_type": installation.connector_type,
                "status": installation.status,
                "auth_status": ConnectorInstallationService().auth_status(installation),
            }
        )


class ConnectorSyncJobViewSet(
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Retrieve connector sync job status (org-scoped)."""

    queryset = ConnectorSyncJob.objects.all().select_related(
        "installation",
        "installation__organization",
    )
    serializer_class = ConnectorSyncJobSerializer
    required_capability = "manage_config"
    read_capability = "manage_config"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        return scope_by_organization(
            super().get_queryset(),
            self.request.user,
            field="installation__organization_id",
        )


def _serialize_speech_center_context(context: dict) -> dict:
    analyses_in = context.get("analyses") or {}
    analyses_out = {}
    for key, row in analyses_in.items():
        analyses_out[key] = (
            SpeechCenterAnalysisSerializer(row).data if row is not None else None
        )
    media = context.get("media")
    transcript = context.get("transcript")
    return {
        "media": SpeechCenterMediaSerializer(media).data if media is not None else None,
        "transcript": (
            SpeechCenterTranscriptSerializer(transcript).data
            if transcript is not None
            else None
        ),
        "status": context.get("status"),
        "speakers": SpeakerSerializer(context.get("speakers") or [], many=True).data,
        "analyses": analyses_out,
        "external_references": ExternalReferenceSerializer(
            context.get("external_references") or [],
            many=True,
        ).data,
    }


class SpeechCenterViewSet(viewsets.ViewSet):
    """
    Unified Speech Center access for host applications (Phase 4.5.1+).

    GET /speech-center/?external_system=&external_type=&external_id=
    GET /speech-center/{transcript_id}/timeline/
    GET /speech-center/{transcript_id}/intelligence/
    POST|GET /speech-center/ask/  (RAG foundation, Phase 4.5.6)
    """

    required_capability = "view_transcript"
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "post", "head", "options"]

    def list(self, request):
        from turing.auth.tenancy import resolve_organization
        from turing.services.speech_center import (
            SpeechCenterService,
            require_external_lookup_params,
        )

        try:
            system, type_, eid = require_external_lookup_params(
                external_system=request.query_params.get("external_system"),
                external_type=request.query_params.get("external_type"),
                external_id=request.query_params.get("external_id"),
            )
            organization = resolve_organization(
                organization_id=request.query_params.get("organization_id"),
                user=request.user,
                capability="view_transcript",
            )
            context = SpeechCenterService().get_by_external_reference(
                organization=organization,
                external_system=system,
                external_type=type_,
                external_id=eid,
                user=request.user,
            )
        except TuringError as exc:
            return _error_response(exc)
        return Response(_serialize_speech_center_context(context))

    @action(detail=False, methods=["get", "post"], url_path="ask")
    def ask(self, request):
        """
        RAG ask foundation (Phase 4.5.6).

        Retrieves org-scoped semantic search hits, builds context, and asks the
        configured LLM provider (default: null / unavailable).
        """
        from turing.auth.tenancy import resolve_organization
        from turing.domain.exceptions import ValidationError
        from turing.services.rag import RAGService

        data = request.data if request.method == "POST" else request.query_params
        question = (data.get("question") or data.get("q") or "").strip()
        if not question:
            return _error_response(
                ValidationError("question is required.")
            )

        try:
            organization = resolve_organization(
                organization_id=data.get("organization_id")
                or request.query_params.get("organization_id"),
                user=request.user,
                capability="view_transcript",
            )
        except TuringError as exc:
            return _error_response(exc)

        filters = {
            "external_system": (data.get("external_system") or "").strip(),
            "external_type": (data.get("external_type") or "").strip(),
            "external_id": (data.get("external_id") or "").strip(),
        }
        payload = RAGService().answer(
            question,
            organization,
            filters=filters,
        )
        return Response(payload)

    @action(detail=True, methods=["get"], url_path="timeline")
    def timeline(self, request, pk=None):
        from turing.services.speech_center import SpeechCenterService

        service = SpeechCenterService()
        try:
            transcript = service.get_transcript_for_user(pk, user=request.user)
            payload = service.get_timeline(transcript, user=request.user)
        except TuringError as exc:
            return _error_response(exc)

        return Response(
            {
                "transcript_id": payload["transcript_id"],
                "status": payload["status"],
                "speakers": SpeakerSerializer(payload["speakers"], many=True).data,
                "segments": TranscriptSegmentSerializer(
                    payload["segments"], many=True
                ).data,
                "timestamps": payload["timestamps"],
                "analysis_references": SpeechCenterTimelineAnalysisRefSerializer(
                    payload["analysis_references"],
                    many=True,
                ).data,
            }
        )

    @action(detail=True, methods=["get"], url_path="intelligence")
    def intelligence(self, request, pk=None):
        from turing.services.speech_center import SpeechCenterService

        service = SpeechCenterService()
        try:
            transcript = service.get_transcript_for_user(pk, user=request.user)
            payload = service.get_latest_intelligence(transcript, user=request.user)
        except TuringError as exc:
            return _error_response(exc)

        generated_at = payload.get("generated_at")
        return Response(
            {
                "transcript_id": str(transcript.id),
                "intelligence": {
                    "summary": payload.get("summary"),
                    "topics": payload.get("topics"),
                    "action_items": payload.get("action_items"),
                },
                "generated_at": (
                    generated_at.isoformat() if generated_at is not None else None
                ),
            }
        )


class SemanticSearchViewSet(viewsets.ViewSet):
    """
    Semantic search API (Phase 4.5.4).

    Ranks org-scoped transcript segments via the configured
    ``SemanticSearchProvider`` (default: pgvector).
    """

    required_capability = "view_transcript"
    read_capability = "view_transcript"
    permission_classes = [IsAuthenticated, HasTuringCapability]
    http_method_names = ["get", "head", "options"]

    def list(self, request):
        from turing.auth.tenancy import resolve_organization
        from turing.search.registry import SemanticSearchRegistry

        try:
            organization = resolve_organization(
                organization_id=request.query_params.get("organization_id"),
                user=request.user,
                capability="view_transcript",
            )
        except TuringError as exc:
            return _error_response(exc)

        q = (request.query_params.get("q") or "").strip()
        try:
            limit = int(request.query_params.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        filters = {
            "external_system": (request.query_params.get("external_system") or "").strip(),
            "external_type": (request.query_params.get("external_type") or "").strip(),
            "external_id": (request.query_params.get("external_id") or "").strip(),
        }

        provider = SemanticSearchRegistry.create()
        result = provider.search(
            q,
            organization_id=organization.id,
            limit=limit,
            filters=filters,
        )

        results = []
        for hit in result.hits:
            meta = hit.metadata or {}
            start_ms = meta.get("start_ms")
            end_ms = meta.get("end_ms")
            results.append(
                {
                    "transcript_id": meta.get("transcript_id") or "",
                    "segment_id": meta.get("segment_id") or hit.object_id,
                    "speaker": meta.get("speaker") or "",
                    "start_time": start_ms,
                    "end_time": end_ms,
                    "text": meta.get("text") or "",
                    "score": hit.score,
                    "external_references": meta.get("external_references") or [],
                }
            )

        return Response(
            {
                "results": results,
                "provider": result.provider or provider.code,
            }
        )
