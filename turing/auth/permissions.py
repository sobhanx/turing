from __future__ import annotations

from rest_framework.permissions import BasePermission, SAFE_METHODS

from turing.auth.roles import user_has_capability


class HasTuringCapability(BasePermission):
    capability: str = "view_transcript"

    def has_permission(self, request, view) -> bool:
        capability = getattr(view, "required_capability", None) or self.capability
        if request.method in SAFE_METHODS:
            capability = getattr(view, "read_capability", "view_transcript")
        return user_has_capability(request.user, capability)


class CanUploadMedia(HasTuringCapability):
    capability = "upload_media"


class CanManageJobs(HasTuringCapability):
    capability = "manage_jobs"


class CanEditTranscript(HasTuringCapability):
    capability = "edit_transcript"


class CanReviewTranscript(HasTuringCapability):
    capability = "review_transcript"
