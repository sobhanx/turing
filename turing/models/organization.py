from __future__ import annotations

from django.db import models

from turing.models.media import TimeStampedModel

DEFAULT_ORGANIZATION_SLUG = "default"


class Organization(TimeStampedModel):
    """
    Tenant / data-ownership boundary for Turing resources.

    Host products map their own org/account IDs onto Turing organizations
    (by slug or external_key) without Turing owning billing or invites.
    """

    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=64, unique=True)
    external_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Optional host-project key (mirrors historical tenant_key).",
    )
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def get_default(cls) -> Organization:
        """Ensure the local/demo default organization exists."""
        org, _ = cls.objects.get_or_create(
            slug=DEFAULT_ORGANIZATION_SLUG,
            defaults={
                "name": "Default",
                "external_key": "",
                "is_active": True,
                "notes": "Seeded default organization for local/demo use.",
            },
        )
        return org
