from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from turing.api.filters import MediaAssetFilter, ProcessingJobFilter, TranscriptFilter
from turing.api.serializers import (
    CreateTranscriptionJobSerializer,
    MediaAssetSerializer,
    MediaUploadSerializer,
    ProcessingJobSerializer,
    ProcessingLogSerializer,
    SegmentUpdateSerializer,
    SpeakerRenameSerializer,
    SpeakerSerializer,
    TranscriptListSerializer,
    TranscriptRevisionSerializer,
    TranscriptSegmentSerializer,
    TranscriptSerializer,
)
from turing.auth.permissions import (
    CanApproveTranscript,
    CanEditTranscript,
    CanManageJobs,
    CanUploadMedia,
    HasTuringCapability,
)
from turing.auth.tenancy import scope_by_organization
from turing.domain.exceptions import PermissionDeniedError, TuringError
from turing.models import MediaAsset, ProcessingJob, Speaker, Transcript, TranscriptSegment
from turing.services.job_orchestrator import JobOrchestrator
from turing.services.media import MediaService
from turing.services.transcript import TranscriptService


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
        return scope_by_organization(super().get_queryset(), self.request.user)

    def get_permissions(self):
        if self.action == "create":
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
        except TuringError as exc:
            return _error_response(exc)
        return Response(MediaAssetSerializer(asset).data, status=status.HTTP_201_CREATED)


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
        if self.action == "retrieve":
            return qs.prefetch_related("speakers", "segments__speaker")
        return qs

    def get_permissions(self):
        if self.action == "submit_review":
            # Editors (and reviewers) may submit; only reviewers/admins approve.
            return [IsAuthenticated(), CanEditTranscript()]
        if self.action == "approve":
            return [IsAuthenticated(), CanApproveTranscript()]
        return super().get_permissions()

    @action(detail=True, methods=["get"])
    def revisions(self, request, pk=None):
        transcript = self.get_object()
        return Response(
            TranscriptRevisionSerializer(transcript.revisions.all(), many=True).data
        )

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
