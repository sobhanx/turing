from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from turing.domain.enums import TuringRole
from turing.models.configuration import PlatformConfiguration, SpeechProviderConfig


@receiver(post_migrate)
def seed_turing_defaults(sender, **kwargs) -> None:
    """Seed singleton config + Speechmatics provider row after migrate."""
    if sender.name != "turing":
        return

    PlatformConfiguration.get_solo()
    SpeechProviderConfig.objects.get_or_create(
        code="speechmatics",
        defaults={
            "name": "Speechmatics",
            "is_active": True,
            "priority": 10,
            "base_url": "https://asr.api.speechmatics.com/v2",
            "operating_point": "enhanced",
            "enable_diarization": True,
        },
    )

    # Optional: attach membership for superusers if none exists
    User = get_user_model()
    for user in User.objects.filter(is_superuser=True):
        from turing.models import TuringMembership

        TuringMembership.objects.get_or_create(
            user=user,
            defaults={"role": TuringRole.ADMIN, "is_active": True},
        )
