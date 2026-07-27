# Phase 3.2 — Transcript intelligence (derived AI analyses)

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0008_provider_webhooks"),
    ]

    operations = [
        migrations.CreateModel(
            name="TranscriptAnalysis",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "analysis_type",
                    models.CharField(
                        choices=[
                            ("summary", "Summary"),
                            ("action_items", "Action items"),
                            ("topics", "Topics"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "content",
                    models.JSONField(
                        default=dict,
                        help_text="Provider-specific structured output (summary, action items, topics, etc.).",
                    ),
                ),
                ("provider", models.CharField(db_index=True, max_length=64)),
                ("model_name", models.CharField(blank=True, default="", max_length=128)),
                (
                    "organization",
                    models.ForeignKey(
                        help_text="Copied from transcript at persist time for tenant-safe queries.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transcript_analyses",
                        to="turing.organization",
                    ),
                ),
                (
                    "transcript",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analyses",
                        to="turing.transcript",
                    ),
                ),
            ],
            options={
                "verbose_name": "Transcript analysis",
                "verbose_name_plural": "Transcript analyses",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="transcriptanalysis",
            index=models.Index(
                fields=["transcript", "analysis_type", "-created_at"],
                name="turing_analysis_transcript_type_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="transcriptanalysis",
            index=models.Index(
                fields=["organization", "analysis_type"],
                name="turing_analysis_org_type_idx",
            ),
        ),
    ]
