from __future__ import annotations

from django.db import models


class SourceType(models.TextChoices):
    UPLOAD = "upload", "Upload"
    URL = "url", "External URL"
    STREAM = "stream", "Stream"  # reserved for future real-time


class UseCase(models.TextChoices):
    """Product scenarios sharing the same transcription engine."""

    MEETING = "meeting", "Meeting transcription"
    CRM_CALL = "crm_call", "CRM call transcription"
    INTERVIEW = "interview", "Interview transcription"
    VOICE_FILE = "voice_file", "Voice file transcription"
    GENERIC = "generic", "Generic transcription"


class Capability(models.TextChoices):
    STT = "stt", "Speech-to-Text"
    # Future: summarize, sentiment, translation, etc.


class JobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    PARTIAL = "partial", "Partial"


class TranscriptStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In review"
    APPROVED = "approved", "Approved"
    ARCHIVED = "archived", "Archived"


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In progress"
    APPROVED = "approved", "Approved"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    REJECTED = "rejected", "Rejected"


class ReviewDecisionType(models.TextChoices):
    APPROVE = "approve", "Approve"
    REQUEST_CHANGES = "request_changes", "Request changes"
    REJECT = "reject", "Reject"


class RevisionSource(models.TextChoices):
    PROVIDER = "provider", "Provider"
    HUMAN = "human", "Human"
    SYSTEM = "system", "System"


class AnalysisType(models.TextChoices):
    """Derived AI analysis attached to a transcript (never mutates raw content)."""

    SUMMARY = "summary", "Summary"
    ACTION_ITEMS = "action_items", "Action items"
    TOPICS = "topics", "Topics"


class LogLevel(models.TextChoices):
    DEBUG = "debug", "Debug"
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class TuringRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    REVIEWER = "reviewer", "Reviewer"
    EDITOR = "editor", "Editor"
    USER = "user", "User"
    VIEWER = "viewer", "Viewer"


class StorageBackend(models.TextChoices):
    LOCAL = "local", "Local filesystem"
    S3 = "s3", "AWS S3"
    AZURE = "azure", "Azure Blob Storage"
    GCS = "gcs", "Google Cloud Storage"


class ArtifactKind(models.TextChoices):
    NORMALIZED = "normalized", "Normalized audio"


class ArtifactStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class IngestStatus(models.TextChoices):
    """Outcome of pre-STT media ingestion for a processing job."""

    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class ExternalReferenceTarget(models.TextChoices):
    """Turing object kinds that can be linked to a host external reference."""

    MEDIA = "media", "Media asset"
    TRANSCRIPT = "transcript", "Transcript"


class OutboxEventStatus(models.TextChoices):
    """Durable outbox delivery lifecycle (Phase 4.2.1)."""

    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


class OutboundWebhookDeliveryStatus(models.TextChoices):
    """Outbound host webhook delivery lifecycle (Phase 4.2.2)."""

    PENDING = "pending", "Pending"
    DELIVERING = "delivering", "Delivering"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"
