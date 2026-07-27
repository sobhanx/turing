# Phase 3.3 — Universal audio ingestion

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0009_transcript_analysis"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfiguration",
            name="max_duration_ms",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Maximum allowed audio duration in ms (0 = no limit).",
            ),
        ),
        migrations.AddField(
            model_name="platformconfiguration",
            name="normalization_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Inspect and normalize audio before STT when ffmpeg is available.",
            ),
        ),
        migrations.AddField(
            model_name="platformconfiguration",
            name="poll_timeout_multiplier",
            field=models.FloatField(
                default=2.0,
                help_text="Poll timeout floor = max(base timeout, duration_seconds * multiplier).",
            ),
        ),
        migrations.AddField(
            model_name="processingjob",
            name="expected_duration_ms",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Detected audio duration used for poll timeout scaling.",
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="MediaProcessingArtifact",
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
                    "kind",
                    models.CharField(
                        choices=[("normalized", "Normalized audio")],
                        db_index=True,
                        default="normalized",
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("ready", "Ready"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "storage_backend",
                    models.CharField(
                        choices=[
                            ("local", "Local filesystem"),
                            ("s3", "AWS S3"),
                            ("azure", "Azure Blob Storage"),
                            ("gcs", "Google Cloud Storage"),
                        ],
                        default="local",
                        max_length=16,
                    ),
                ),
                ("object_key", models.CharField(blank=True, default="", max_length=512)),
                ("byte_size", models.BigIntegerField(default=0)),
                ("checksum", models.CharField(blank=True, default="", max_length=64)),
                ("content_type", models.CharField(blank=True, default="", max_length=128)),
                ("audio_format", models.CharField(blank=True, default="", max_length=32)),
                ("audio_codec", models.CharField(blank=True, default="", max_length=64)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("sample_rate_hz", models.PositiveIntegerField(blank=True, null=True)),
                ("channels", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("probe_metadata", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "media",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="processing_artifacts",
                        to="turing.mediaasset",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        help_text="Copied from media for tenant-safe queries.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="media_processing_artifacts",
                        to="turing.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Media processing artifact",
                "verbose_name_plural": "Media processing artifacts",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="processingjob",
            name="ingest_artifact",
            field=models.ForeignKey(
                blank=True,
                help_text="Normalized artifact used for STT submit, when available.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="jobs",
                to="turing.mediaprocessingartifact",
            ),
        ),
        migrations.AddIndex(
            model_name="mediaprocessingartifact",
            index=models.Index(
                fields=["media", "kind", "-created_at"],
                name="turing_artifact_media_kind_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mediaprocessingartifact",
            index=models.Index(
                fields=["organization", "status"],
                name="turing_artifact_org_status_idx",
            ),
        ),
    ]
