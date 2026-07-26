from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS

from turing.auth.roles import user_has_capability
from turing.models import MediaAsset, ProcessingJob, Speaker, Transcript, TranscriptSegment


class HasTuringCapability(BasePermission):
    capability: str = "view_transcript"

    def has_permission(self, request, view) -> bool:
        capability = getattr(view, "required_capability", None) or self.capability
        if request.method in SAFE_METHODS:
            capability = getattr(view, "read_capability", "view_transcript")
        return user_has_capability(request.user, capability)

    def has_object_permission(self, request, view, obj) -> bool:
        capability = getattr(view, "required_capability", None) or self.capability
        if request.method in SAFE_METHODS:
            capability = getattr(view, "read_capability", "view_transcript")
        organization = _organization_from_obj(obj)
        if organization is None:
            # Legacy rows without org: staff/superuser only (via role fallback).
            return user_has_capability(request.user, capability)
        return user_has_capability(
            request.user, capability, organization=organization
        )


class CanUploadMedia(HasTuringCapability):
    capability = "upload_media"

    def has_permission(self, request, view) -> bool:
        return user_has_capability(request.user, self.capability)


class CanManageJobs(HasTuringCapability):
    capability = "manage_jobs"

    def has_permission(self, request, view) -> bool:
        return user_has_capability(request.user, self.capability)


class CanEditTranscript(HasTuringCapability):
    capability = "edit_transcript"

    def has_permission(self, request, view) -> bool:
        return user_has_capability(request.user, self.capability)


class CanReviewTranscript(HasTuringCapability):
    capability = "review_transcript"

    def has_permission(self, request, view) -> bool:
        return user_has_capability(request.user, self.capability)


class CanApproveTranscript(HasTuringCapability):
    capability = "approve_transcript"

    def has_permission(self, request, view) -> bool:
        return user_has_capability(request.user, self.capability)


def _organization_from_obj(obj):
    if obj is None:
        return None
    if isinstance(obj, (MediaAsset, ProcessingJob, Transcript)):
        return getattr(obj, "organization", None)
    if isinstance(obj, TranscriptSegment):
        transcript = getattr(obj, "transcript", None)
        return getattr(transcript, "organization", None) if transcript else None
    if isinstance(obj, Speaker):
        transcript = getattr(obj, "transcript", None)
        return getattr(transcript, "organization", None) if transcript else None
    return getattr(obj, "organization", None)
