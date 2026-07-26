from __future__ import annotations

from django.conf import settings
from django.db import models

from turing.domain.enums import TuringRole
from turing.models.media import TimeStampedModel


class TuringMembership(TimeStampedModel):
    """
    Maps a host User to a Turing role.

    Host projects may also map their own RBAC onto Turing capabilities;
    this model provides a built-in Admin-managed role system.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="turing_membership",
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

    def __str__(self) -> str:
        return f"{self.user} → {self.role}"
