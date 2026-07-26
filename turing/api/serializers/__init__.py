from __future__ import annotations

from rest_framework import serializers

from turing.domain.enums import UseCase
from turing.models import (
    MediaAsset,
    ProcessingJob,
    ProcessingLog,
    Speaker,
    Transcript,
    TranscriptRevision,
    TranscriptSegment,
)


class MediaAssetSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

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
            "checksum",
            "external_url",
            "tenant_key",
            "metadata",
            "display_name",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "storage_backend",
            "byte_size",
            "checksum",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]


class MediaUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=False)
    external_url = serializers.URLField(required=False, allow_blank=True)
    use_case = serializers.ChoiceField(choices=UseCase.choices, default=UseCase.GENERIC)
    tenant_key = serializers.CharField(required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False)

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

    class Meta:
        model = Transcript
        fields = [
            "id",
            "job",
            "media",
            "language_code",
            "status",
            "full_text",
            "version",
            "is_primary",
            "confidence_avg",
            "approved_at",
            "approved_by",
            "speakers",
            "segments",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "job",
            "media",
            "full_text",
            "version",
            "confidence_avg",
            "approved_at",
            "approved_by",
            "created_at",
            "updated_at",
        ]


class TranscriptListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transcript
        fields = [
            "id",
            "job",
            "media",
            "language_code",
            "status",
            "version",
            "is_primary",
            "confidence_avg",
            "created_at",
            "updated_at",
        ]


class SegmentUpdateSerializer(serializers.Serializer):
    text = serializers.CharField(required=False)
    speaker_id = serializers.UUIDField(required=False)
    start_ms = serializers.IntegerField(required=False, min_value=0)
    end_ms = serializers.IntegerField(required=False, min_value=0)


class SpeakerRenameSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=128)
