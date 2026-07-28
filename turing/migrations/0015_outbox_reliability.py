# Phase 4.2.3 — outbox / webhook stuck recovery fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0014_outbound_webhooks"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboxevent",
            name="processing_started_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Set when status becomes PROCESSING; used for stuck recovery.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="recovery_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="How many times this row was reset from stuck PROCESSING.",
            ),
        ),
        migrations.AddField(
            model_name="webhookdelivery",
            name="processing_started_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Set when status becomes DELIVERING; used for stuck recovery.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="webhookdelivery",
            name="recovery_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="How many times this row was reset from stuck DELIVERING.",
            ),
        ),
        migrations.AddIndex(
            model_name="outboxevent",
            index=models.Index(
                fields=["status", "processing_started_at"],
                name="turing_outbox_stuck_scan",
            ),
        ),
        migrations.AddIndex(
            model_name="webhookdelivery",
            index=models.Index(
                fields=["status", "processing_started_at"],
                name="turing_whdel_stuck_scan",
            ),
        ),
    ]
