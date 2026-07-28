# Phase 4.5.3 — semantic search Embedding foundation

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0018_connector_hardening"),
    ]

    operations = [
        migrations.CreateModel(
            name="Embedding",
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
                    "object_type",
                    models.CharField(
                        choices=[
                            ("transcript_segment", "Transcript segment"),
                            ("transcript", "Transcript"),
                        ],
                        db_index=True,
                        max_length=64,
                    ),
                ),
                ("object_id", models.CharField(db_index=True, max_length=64)),
                (
                    "content_hash",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64
                    ),
                ),
                (
                    "vector",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Provider-neutral embedding placeholder (list of floats).",
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "organization",
                    models.ForeignKey(
                        help_text="Owning organization (data boundary). Required.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="embeddings",
                        to="turing.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Embedding",
                "verbose_name_plural": "Embeddings",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="embedding",
            index=models.Index(
                fields=["organization", "object_type"],
                name="turing_embed_org_type",
            ),
        ),
        migrations.AddConstraint(
            model_name="embedding",
            constraint=models.UniqueConstraint(
                fields=("organization", "object_type", "object_id"),
                name="turing_embedding_org_object_uniq",
            ),
        ),
    ]
