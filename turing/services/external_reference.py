from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, transaction

from turing.auth.tenancy import assert_organization_access, scope_by_organization
from turing.domain.exceptions import NotFoundError, ValidationError
from turing.models import ExternalReference, MediaAsset, Organization, Transcript


class ExternalReferenceService:
    """
    Attach host-application object keys to Turing media/transcripts.

    Organization must always match the target object's organization. Validation
    runs in this service — callers must not rely on model.clean() alone.
    """

    def attach_to_media(
        self,
        media: MediaAsset,
        *,
        external_system: str,
        external_type: str,
        external_id: str,
        user: AbstractBaseUser | None = None,
        metadata: dict | None = None,
    ) -> tuple[ExternalReference, bool]:
        system, type_, eid = self._normalize_key(external_system, external_type, external_id)
        self._assert_write_access(user, media.organization, capability="upload_media")

        try:
            with transaction.atomic():
                ref, created = ExternalReference.objects.get_or_create(
                    organization=media.organization,
                    external_system=system,
                    external_type=type_,
                    external_id=eid,
                    media=media,
                    defaults={"metadata": dict(metadata or {})},
                )
        except IntegrityError as exc:
            raise ValidationError(
                "External reference conflicts with an existing link."
            ) from exc
        return ref, created

    def attach_to_transcript(
        self,
        transcript: Transcript,
        *,
        external_system: str,
        external_type: str,
        external_id: str,
        user: AbstractBaseUser | None = None,
        metadata: dict | None = None,
    ) -> tuple[ExternalReference, bool]:
        system, type_, eid = self._normalize_key(external_system, external_type, external_id)
        self._assert_write_access(user, transcript.organization, capability="edit_transcript")
        if transcript.organization_id is None:
            raise ValidationError("Transcript has no organization.")

        try:
            with transaction.atomic():
                ref, created = ExternalReference.objects.get_or_create(
                    organization=transcript.organization,
                    external_system=system,
                    external_type=type_,
                    external_id=eid,
                    transcript=transcript,
                    defaults={"metadata": dict(metadata or {})},
                )
        except IntegrityError as exc:
            raise ValidationError(
                "External reference conflicts with an existing link."
            ) from exc
        return ref, created

    def detach(
        self,
        reference: ExternalReference,
        *,
        user: AbstractBaseUser | None = None,
    ) -> None:
        capability = "upload_media" if reference.media_id else "edit_transcript"
        self._assert_write_access(user, reference.organization, capability=capability)
        reference.delete()

    def get(
        self,
        reference_id: str,
        *,
        user: AbstractBaseUser | None = None,
    ) -> ExternalReference:
        qs = ExternalReference.objects.select_related(
            "organization",
            "media",
            "transcript",
        )
        if user is not None:
            qs = self.scope_queryset(qs, user)
        try:
            return qs.get(pk=reference_id)
        except ExternalReference.DoesNotExist as exc:
            raise NotFoundError(f"ExternalReference '{reference_id}' not found.") from exc

    def list_for_media(
        self,
        media: MediaAsset,
        *,
        user: AbstractBaseUser | None = None,
    ):
        if user is not None:
            assert_organization_access(
                user,
                media.organization,
                capability="view_transcript",
            )
        return ExternalReference.objects.filter(media=media).order_by("-created_at")

    def list_for_transcript(
        self,
        transcript: Transcript,
        *,
        user: AbstractBaseUser | None = None,
    ):
        if user is not None:
            assert_organization_access(
                user,
                transcript.organization,
                capability="view_transcript",
            )
        return ExternalReference.objects.filter(transcript=transcript).order_by(
            "-created_at"
        )

    def lookup(
        self,
        *,
        organization: Organization,
        external_system: str,
        external_type: str,
        external_id: str,
        user: AbstractBaseUser | None = None,
    ):
        system, type_, eid = self._normalize_key(external_system, external_type, external_id)
        if user is not None:
            assert_organization_access(
                user,
                organization,
                capability="view_transcript",
            )
        return ExternalReference.objects.filter(
            organization=organization,
            external_system=system,
            external_type=type_,
            external_id=eid,
        ).select_related("media", "transcript").order_by("-created_at")

    def scope_queryset(self, queryset, user):
        return scope_by_organization(queryset, user, field="organization_id")

    def _assert_write_access(
        self,
        user: AbstractBaseUser | None,
        organization: Organization,
        *,
        capability: str,
    ) -> None:
        if user is not None:
            assert_organization_access(user, organization, capability=capability)

    def _assert_org_matches(self, organization_id, target_org_id, *, label: str) -> None:
        if organization_id != target_org_id:
            raise ValidationError(
                f"Organization must match the linked {label} organization."
            )

    def create_for_target(
        self,
        *,
        organization: Organization,
        external_system: str,
        external_type: str,
        external_id: str,
        media: MediaAsset | None = None,
        transcript: Transcript | None = None,
        user: AbstractBaseUser | None = None,
        metadata: dict | None = None,
    ) -> tuple[ExternalReference, bool]:
        """
        Create a reference with explicit target validation.

        Prefer ``attach_to_media`` / ``attach_to_transcript`` for normal callers.
        """
        has_media = media is not None
        has_transcript = transcript is not None
        if has_media == has_transcript:
            raise ValidationError("Exactly one of 'media' or 'transcript' must be set.")

        if media is not None:
            if media.organization_id != organization.id:
                raise ValidationError(
                    "Organization must match the linked media organization."
                )
            return self.attach_to_media(
                media,
                external_system=external_system,
                external_type=external_type,
                external_id=external_id,
                user=user,
                metadata=metadata,
            )

        assert transcript is not None
        if transcript.organization_id != organization.id:
            raise ValidationError(
                "Organization must match the linked transcript organization."
            )
        return self.attach_to_transcript(
            transcript,
            external_system=external_system,
            external_type=external_type,
            external_id=external_id,
            user=user,
            metadata=metadata,
        )

    @staticmethod
    def _normalize_key(
        external_system: str,
        external_type: str,
        external_id: str,
    ) -> tuple[str, str, str]:
        system = (external_system or "").strip()
        type_ = (external_type or "").strip()
        eid = (external_id or "").strip()
        if not system:
            raise ValidationError("external_system is required.")
        if not type_:
            raise ValidationError("external_type is required.")
        if not eid:
            raise ValidationError("external_id is required.")
        if len(system) > 64:
            raise ValidationError("external_system must be at most 64 characters.")
        if len(type_) > 64:
            raise ValidationError("external_type must be at most 64 characters.")
        if len(eid) > 255:
            raise ValidationError("external_id must be at most 255 characters.")
        return system, type_, eid
