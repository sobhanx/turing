# Phase 4.2.2 — outbound webhook subscription + delivery

import django.db.models.deletion
import turing.security.fields
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0013_outbox_event"),
    ]

    operations = [
        migrations.CreateModel(
            name="WebhookSubscription",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=128)),
                ("url", models.URLField(max_length=2048)),
                (
                    "secret",
                    turing.security.fields.EncryptedCharField(
                        blank=True,
                        default="",
                        help_text=(
                            "HMAC signing secret (encrypted at rest). "
                            "Never expose in API/Admin."
                        ),
                        max_length=512,
                    ),
                ),
                (
                    "subscribed_events",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            'Event names to receive, e.g. ["transcript.created"]. '
                            'Use ["*"] for all.'
                        ),
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="webhook_subscriptions",
                        to="turing.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Webhook subscription",
                "verbose_name_plural": "Webhook subscriptions",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="WebhookDelivery",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("delivering", "Delivering"),
                            ("delivered", "Delivered"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                (
                    "response_status_code",
                    models.PositiveSmallIntegerField(blank=True, null=True),
                ),
                ("response_body_preview", models.TextField(blank=True, default="")),
                ("last_error", models.TextField(blank=True, default="")),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                (
                    "outbox_event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbound_webhook_deliveries",
                        to="turing.outboxevent",
                    ),
                ),
                (
                    "subscription",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="turing.webhooksubscription",
                    ),
                ),
            ],
            options={
                "verbose_name": "Webhook delivery",
                "verbose_name_plural": "Webhook deliveries",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="webhooksubscription",
            index=models.Index(
                fields=["organization", "is_active"],
                name="turing_whsub_org_active",
            ),
        ),
        migrations.AddIndex(
            model_name="webhookdelivery",
            index=models.Index(
                fields=["status", "created_at"],
                name="turing_whdel_status",
            ),
        ),
        migrations.AddIndex(
            model_name="webhookdelivery",
            index=models.Index(
                fields=["subscription", "-created_at"],
                name="turing_whdel_sub",
            ),
        ),
        migrations.AddConstraint(
            model_name="webhookdelivery",
            constraint=models.UniqueConstraint(
                fields=("subscription", "outbox_event"),
                name="turing_whdel_sub_outbox_uniq",
            ),
        ),
    ]
