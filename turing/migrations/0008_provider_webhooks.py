# Phase 3.1 — Provider webhook delivery audit + platform webhook settings

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0007_phase_2_9_2_hardening"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfiguration",
            name="webhook_mode",
            field=models.CharField(
                choices=[("off", "Off"), ("augment", "Augment (webhooks + polling)")],
                default="augment",
                help_text="Augment: register provider webhooks while keeping poll-based status checks.",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="platformconfiguration",
            name="webhook_base_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text=(
                    "Public base URL for provider callbacks (e.g. https://turing.example.com). "
                    "Required for webhook registration when mode is augment."
                ),
                max_length=512,
            ),
        ),
        migrations.CreateModel(
            name="ProviderWebhookDelivery",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider_code", models.CharField(db_index=True, max_length=64)),
                ("external_job_id", models.CharField(db_index=True, max_length=255)),
                ("status_param", models.CharField(blank=True, default="", max_length=64)),
                ("dedupe_key", models.CharField(max_length=64)),
                ("payload_hash", models.CharField(blank=True, default="", max_length=64)),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("processed", "Processed"),
                            ("duplicate", "Duplicate"),
                            ("ignored", "Ignored"),
                            ("unknown_job", "Unknown job"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("raw_metadata", models.JSONField(blank=True, default=dict)),
                (
                    "processing_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="webhook_deliveries",
                        to="turing.processingjob",
                    ),
                ),
            ],
            options={
                "verbose_name": "Provider webhook delivery",
                "verbose_name_plural": "Provider webhook deliveries",
            },
        ),
        migrations.AddIndex(
            model_name="providerwebhookdelivery",
            index=models.Index(
                fields=["provider_code", "external_job_id", "-created_at"],
                name="turing_webhook_provider_job_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="providerwebhookdelivery",
            constraint=models.UniqueConstraint(
                fields=("provider_code", "dedupe_key"),
                name="turing_webhook_delivery_dedupe_uniq",
            ),
        ),
    ]
