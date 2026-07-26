from __future__ import annotations

from django.conf import settings
from django.db import models

from turing.domain.enums import TuringRole
from turing.models.media import TimeStampedModel


class TuringMembership(TimeStampedModel):
    """
    Connects a host User to an Organization with a Turing role.

    A user may belong to multiple organizations with different roles.
    Host projects may also map their own RBAC onto Turing capabilities.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="turing_memberships",
    )
    organization = models.ForeignKey(
        "turing.Organization",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=TuringRole.choices,
        default=TuringRole.USER,
        db_index=True,
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Turing membership"
        verbose_name_plural = "Turing memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="turing_membership_user_org_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "role"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.organization.slug} ({self.role})"
