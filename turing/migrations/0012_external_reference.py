# Phase 4.1.1 — ExternalReference foundation

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0011_ingestion_hardening"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExternalReference",
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
                    "external_system",
                    models.CharField(
                        db_index=True,
                        help_text="Host product namespace (e.g. crm, bank, hr, meetings).",
                        max_length=64,
                    ),
                ),
                (
                    "external_type",
                    models.CharField(
                        db_index=True,
                        help_text="Host object kind (e.g. deal, case, interview, meeting).",
                        max_length=64,
                    ),
                ),
                (
                    "external_id",
                    models.CharField(
                        db_index=True,
                        help_text="Host object primary identifier.",
                        max_length=255,
                    ),
                ),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Optional non-indexed host baggage (not used as the link key).",
                    ),
                ),
                (
                    "media",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_references",
                        to="turing.mediaasset",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        help_text="Owning organization (data boundary). Required.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="external_references",
                        to="turing.organization",
                    ),
                ),
                (
                    "transcript",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="external_references",
                        to="turing.transcript",
                    ),
                ),
            ],
            options={
                "verbose_name": "External reference",
                "verbose_name_plural": "External references",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="externalreference",
            index=models.Index(
                fields=[
                    "organization",
                    "external_system",
                    "external_type",
                    "external_id",
                ],
                name="turing_extref_host_lookup",
            ),
        ),
        migrations.AddIndex(
            model_name="externalreference",
            index=models.Index(
                fields=["media", "-created_at"],
                name="turing_extref_media",
            ),
        ),
        migrations.AddIndex(
            model_name="externalreference",
            index=models.Index(
                fields=["transcript", "-created_at"],
                name="turing_extref_transcript",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalreference",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("media__isnull", False), ("transcript__isnull", True)),
                    models.Q(("media__isnull", True), ("transcript__isnull", False)),
                    _connector="OR",
                ),
                name="turing_extref_exactly_one_target",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalreference",
            constraint=models.UniqueConstraint(
                condition=models.Q(("media__isnull", False)),
                fields=(
                    "organization",
                    "external_system",
                    "external_type",
                    "external_id",
                    "media",
                ),
                name="turing_extref_media_host_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="externalreference",
            constraint=models.UniqueConstraint(
                condition=models.Q(("transcript__isnull", False)),
                fields=(
                    "organization",
                    "external_system",
                    "external_type",
                    "external_id",
                    "transcript",
                ),
                name="turing_extref_transcript_host_uniq",
            ),
        ),
    ]
