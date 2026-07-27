# Phase 3.3b — Ingestion hardening (fail-closed + outcome fields)

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0010_media_ingestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="processingjob",
            name="ingest_error",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Ingestion failure reason when ingest_status is failed.",
            ),
        ),
        migrations.AddField(
            model_name="processingjob",
            name="ingest_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("succeeded", "Succeeded"),
                    ("skipped", "Skipped"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
    ]
