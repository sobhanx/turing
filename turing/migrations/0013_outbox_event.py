# Phase 4.2.1 — durable OutboxEvent foundation

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0012_external_reference"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboxEvent",
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
                ("event_name", models.CharField(db_index=True, max_length=128)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("delivered", "Delivered"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, default="")),
                (
                    "organization",
                    models.ForeignKey(
                        help_text="Owning organization (data boundary). Required.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outbox_events",
                        to="turing.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Outbox event",
                "verbose_name_plural": "Outbox events",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(
                fields=["status", "created_at"],
                name="turing_outbox_pending_scan",
            ),
        ),
    ]
