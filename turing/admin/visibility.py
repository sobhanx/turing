"""
Admin UI visibility policy (presentation only).

Unregisters clutter models from ``django.contrib.admin.site`` without deleting
models, migrations, tables, APIs, or Admin class definitions (tests may still
instantiate those classes against a private ``AdminSite``).
"""

from __future__ import annotations

from django.contrib import admin
from django.db import models

from turing.models import (
    ConnectorSyncJob,
    ExternalReference,
    Meeting,
    ReviewAssignment,
    TuringMembership,
    WebhookDelivery,
    WebhookSubscription,
)

# Hide from the default Admin index / changelists only.
HIDDEN_FROM_ADMIN: tuple[type[models.Model], ...] = (
    Meeting,
    ExternalReference,
    ConnectorSyncJob,
    WebhookDelivery,
    WebhookSubscription,
    ReviewAssignment,
    TuringMembership,
)


def apply_admin_visibility(site: admin.AdminSite | None = None) -> None:
    """Unregister models listed in ``HIDDEN_FROM_ADMIN`` if they are registered."""
    target = site if site is not None else admin.site
    for model in HIDDEN_FROM_ADMIN:
        if target.is_registered(model):
            target.unregister(model)
