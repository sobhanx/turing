from __future__ import annotations

from rest_framework import serializers

from turing.domain.enums import UseCase
from turing.models import (
    ExternalReference,
    MediaAsset,
    ProcessingJob,
    ProcessingLog,
    Speaker,
    Transcript,
    TranscriptAnalysis,
    TranscriptRevision,
    TranscriptSegment,
    ConnectorInstallation,
    ConnectorSyncJob,
    WebhookDelivery,
    WebhookSubscription,
)


class ExternalReferenceSerializer(serializers.ModelSerializer):
    target_kind = serializers.CharField(read_only=True)

    class Meta:
        model = ExternalReference
        fields = [
            "id",
            "organization",
            "external_system",
            "external_type",
            "external_id",
            "media",
            "transcript",
            "target_kind",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ExternalReferenceCreateSerializer(serializers.Serializer):
    external_system = serializers.CharField(max_length=64)
    external_type = serializers.CharField(max_length=64)
    external_id = serializers.CharField(max_length=255)
    metadata = serializers.JSONField(required=False)


class MediaAssetSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    external_references = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "source_type",
            "use_case",
            "storage_backend",
            "file",
            "original_filename",
            "content_type",
            "byte_size",
            "duration_ms",
            "sample_rate_hz",
            "channels",
            "audio_format",
            "audio_codec",
            "checksum",
            "external_url",
            "organization",
            "tenant_key",
            "metadata",
            "external_references",
            "display_name",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "storage_backend",
            "byte_size",
            "duration_ms",
            "sample_rate_hz",
            "channels",
            "audio_format",
            "audio_codec",
            "checksum",
            "organization",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]

    def get_external_references(self, obj):
        refs = getattr(obj, "_prefetched_objects_cache", {}).get("external_references")
        if refs is None:
            refs = obj.external_references.all()
        return ExternalReferenceSerializer(refs, many=True).data


class MediaUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=False)
    external_url = serializers.URLField(required=False, allow_blank=True)
    use_case = serializers.ChoiceField(choices=UseCase.choices, default=UseCase.GENERIC)
    tenant_key = serializers.CharField(required=False, allow_blank=True, default="")
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False)
    external_references = ExternalReferenceCreateSerializer(many=True, required=False)

    def validate(self, attrs):
        if not attrs.get("file") and not attrs.get("external_url"):
            raise serializers.ValidationError("Provide either 'file' or 'external_url'.")
        return attrs


class ProcessingLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingLog
        fields = ["id", "level", "message", "context", "created_at"]


class ProcessingJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingJob
        fields = [
            "id",
            "media",
            "capability",
            "provider_code",
            "status",
            "priority",
            "language_code",
            "options",
            "external_job_id",
            "idempotency_key",
            "attempt_count",
            "max_attempts",
            "error_code",
            "error_message",
            "queued_at",
            "started_at",
            "finished_at",
            "created_by",
            "organization",
            "tenant_key",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "capability",
            "status",
            "external_job_id",
            "attempt_count",
            "error_code",
            "error_message",
            "queued_at",
            "started_at",
            "finished_at",
            "created_by",
            "organization",
            "created_at",
            "updated_at",
        ]


class CreateTranscriptionJobSerializer(serializers.Serializer):
    media_id = serializers.UUIDField()
    provider_code = serializers.CharField(required=False, allow_blank=True)
    language_code = serializers.CharField(required=False, allow_blank=True, default="")
    options = serializers.JSONField(required=False)
    idempotency_key = serializers.CharField(required=False, allow_blank=True, default="")
    priority = serializers.IntegerField(required=False, default=100)
    auto_enqueue = serializers.BooleanField(required=False, default=True)


class SpeakerSerializer(serializers.ModelSerializer):
    resolved_name = serializers.CharField(read_only=True)

    class Meta:
        model = Speaker
        fields = [
            "id",
            "label",
            "display_name",
            "resolved_name",
            "external_speaker_id",
            "confidence",
            "metadata",
        ]
        read_only_fields = ["id", "label", "external_speaker_id", "confidence"]


class TranscriptSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranscriptSegment
        fields = [
            "id",
            "sequence",
            "speaker",
            "start_ms",
            "end_ms",
            "text",
            "confidence",
            "words",
            "is_edited",
        ]
        read_only_fields = ["id", "sequence", "confidence", "words", "is_edited"]


class TranscriptRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranscriptRevision
        fields = [
            "id",
            "revision_number",
            "source",
            "change_summary",
            "diff",
            "created_by",
            "created_at",
        ]


class TranscriptSerializer(serializers.ModelSerializer):
    speakers = SpeakerSerializer(many=True, read_only=True)
    segments = TranscriptSegmentSerializer(many=True, read_only=True)
    external_references = serializers.SerializerMethodField()

    class Meta:
        model = Transcript
        fields = [
            "id",
            "job",
            "media",
            "organization",
            "language_code",
            "status",
            "full_text",
            "version",
            "is_primary",
            "confidence_avg",
            "word_count",
            "approved_at",
            "approved_by",
            "speakers",
            "segments",
            "external_references",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "job",
            "media",
            "organization",
            "full_text",
            "version",
            "confidence_avg",
            "word_count",
            "approved_at",
            "approved_by",
            "created_at",
            "updated_at",
        ]

    def get_external_references(self, obj):
        refs = getattr(obj, "_prefetched_objects_cache", {}).get("external_references")
        if refs is None:
            refs = obj.external_references.all()
        return ExternalReferenceSerializer(refs, many=True).data


class TranscriptListSerializer(serializers.ModelSerializer):
    external_references = serializers.SerializerMethodField()

    class Meta:
        model = Transcript
        fields = [
            "id",
            "job",
            "media",
            "organization",
            "language_code",
            "status",
            "version",
            "is_primary",
            "confidence_avg",
            "word_count",
            "external_references",
            "created_at",
            "updated_at",
        ]

    def get_external_references(self, obj):
        refs = getattr(obj, "_prefetched_objects_cache", {}).get("external_references")
        if refs is None:
            refs = obj.external_references.all()
        return ExternalReferenceSerializer(refs, many=True).data


class SegmentUpdateSerializer(serializers.Serializer):
    text = serializers.CharField(required=False)
    speaker_id = serializers.UUIDField(required=False)
    start_ms = serializers.IntegerField(required=False, min_value=0)
    end_ms = serializers.IntegerField(required=False, min_value=0)


class SpeakerRenameSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=128)


class TranscriptAnalysisSerializer(serializers.ModelSerializer):
    """Read-only derived AI analysis (append-only; never mutates transcript)."""

    class Meta:
        model = TranscriptAnalysis
        fields = [
            "id",
            "transcript",
            "organization",
            "analysis_type",
            "content",
            "provider",
            "model_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WebhookSubscriptionSerializer(serializers.ModelSerializer):
    """Public webhook subscription (never includes signing secret)."""

    class Meta:
        model = WebhookSubscription
        fields = [
            "id",
            "name",
            "url",
            "subscribed_events",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class WebhookSubscriptionWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128)
    url = serializers.URLField(max_length=2048)
    subscribed_events = serializers.ListField(
        child=serializers.CharField(max_length=128),
        allow_empty=False,
    )
    is_active = serializers.BooleanField(required=False, default=True)
    organization_id = serializers.IntegerField(required=False)

    def validate_url(self, value: str) -> str:
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.core.validators import URLValidator

        url = (value or "").strip()
        validator = URLValidator(schemes=["http", "https"])
        try:
            validator(url)
        except DjangoValidationError as exc:
            raise serializers.ValidationError("Enter a valid http(s) URL.") from exc
        return url

    def validate_subscribed_events(self, value: list) -> list[str]:
        from turing.domain.events import SUPPORTED_OUTBOUND_EVENT_NAMES

        if not value:
            raise serializers.ValidationError("Select at least one event (or '*').")
        cleaned: list[str] = []
        for item in value:
            name = str(item).strip()
            if not name:
                raise serializers.ValidationError("Event names must be non-empty.")
            cleaned.append(name)
        unknown = sorted(
            {
                name
                for name in cleaned
                if name != "*" and name not in SUPPORTED_OUTBOUND_EVENT_NAMES
            }
        )
        if unknown:
            raise serializers.ValidationError(
                "Unknown event name(s): "
                + ", ".join(unknown)
                + ". Supported: "
                + ", ".join(sorted(SUPPORTED_OUTBOUND_EVENT_NAMES))
                + ", *."
            )
        return cleaned


class WebhookSubscriptionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128, required=False)
    url = serializers.URLField(max_length=2048, required=False)
    subscribed_events = serializers.ListField(
        child=serializers.CharField(max_length=128),
        allow_empty=False,
        required=False,
    )
    is_active = serializers.BooleanField(required=False)

    def validate_url(self, value: str) -> str:
        return WebhookSubscriptionWriteSerializer().validate_url(value)

    def validate_subscribed_events(self, value: list) -> list[str]:
        return WebhookSubscriptionWriteSerializer().validate_subscribed_events(value)


class WebhookDeliverySerializer(serializers.ModelSerializer):
    """Read-only delivery status (no response body content)."""

    event = serializers.CharField(source="outbox_event.event_name", read_only=True)

    class Meta:
        model = WebhookDelivery
        fields = [
            "id",
            "status",
            "attempts",
            "response_status_code",
            "last_error",
            "delivered_at",
            "created_at",
            "event",
        ]
        read_only_fields = fields


class ConnectorInstallationSerializer(serializers.ModelSerializer):
    """Public connector installation (never includes tokens or raw config)."""

    auth_status = serializers.SerializerMethodField()
    health = serializers.SerializerMethodField()
    last_sync = serializers.SerializerMethodField()

    class Meta:
        model = ConnectorInstallation
        fields = [
            "id",
            "connector_type",
            "name",
            "status",
            "auth_status",
            "health",
            "last_sync",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_auth_status(self, obj: ConnectorInstallation) -> dict:
        from turing.services.connector_installation import ConnectorInstallationService

        return ConnectorInstallationService().auth_status(obj)

    def get_health(self, obj: ConnectorInstallation) -> dict:
        return obj.health_summary()

    def get_last_sync(self, obj: ConnectorInstallation) -> dict | None:
        job = obj.last_sync()
        if job is None:
            return None
        return {
            "id": str(job.id),
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "records_processed": job.records_processed,
            "error": (job.error or "")[:500],
        }

_INSTALLATION_STATUS_CHOICES = [
    "pending",
    "active",
    "expired",
    "revoked",
    "error",
]


class ConnectorInstallationWriteSerializer(serializers.Serializer):
    connector_type = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=128)
    config = serializers.DictField(required=False, default=dict)
    status = serializers.ChoiceField(
        choices=_INSTALLATION_STATUS_CHOICES,
        required=False,
    )
    organization_id = serializers.IntegerField(required=False)


class ConnectorInstallationUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=128, required=False)
    status = serializers.ChoiceField(
        choices=_INSTALLATION_STATUS_CHOICES,
        required=False,
    )
    config = serializers.DictField(required=False)


class ConnectorSyncJobSerializer(serializers.ModelSerializer):
    connector_type = serializers.CharField(
        source="installation.connector_type",
        read_only=True,
    )

    class Meta:
        model = ConnectorSyncJob
        fields = [
            "id",
            "installation",
            "connector_type",
            "status",
            "started_at",
            "finished_at",
            "records_processed",
            "error",
            "created_at",
        ]
        read_only_fields = fields


class SpeechCenterMediaSerializer(serializers.ModelSerializer):
    """Media summary for Speech Center (no nested external refs)."""

    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "source_type",
            "use_case",
            "original_filename",
            "content_type",
            "byte_size",
            "duration_ms",
            "external_url",
            "organization",
            "display_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SpeechCenterTranscriptSerializer(serializers.ModelSerializer):
    """Transcript summary for Speech Center (segments live on timeline)."""

    class Meta:
        model = Transcript
        fields = [
            "id",
            "job",
            "media",
            "organization",
            "language_code",
            "status",
            "full_text",
            "version",
            "is_primary",
            "confidence_avg",
            "word_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SpeechCenterAnalysisSerializer(serializers.ModelSerializer):
    """Latest intelligence row for one analysis type."""

    class Meta:
        model = TranscriptAnalysis
        fields = [
            "id",
            "analysis_type",
            "content",
            "provider",
            "model_name",
            "created_at",
        ]
        read_only_fields = fields


class SpeechCenterTimelineAnalysisRefSerializer(serializers.Serializer):
    id = serializers.CharField()
    analysis_type = serializers.CharField()
    provider = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
