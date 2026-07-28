# Phase 4.3.5 — OAuth connector credential foundation

import django.db.models.deletion
import uuid
from django.db import migrations, models


def migrate_disabled_to_revoked(apps, schema_editor):
    ConnectorInstallation = apps.get_model("turing", "ConnectorInstallation")
    ConnectorInstallation.objects.filter(status="disabled").update(status="revoked")


def reverse_revoked_to_disabled(apps, schema_editor):
    ConnectorInstallation = apps.get_model("turing", "ConnectorInstallation")
    ConnectorInstallation.objects.filter(status="revoked").update(status="disabled")


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0016_connector_foundation"),
    ]

    operations = [
        migrations.RunPython(migrate_disabled_to_revoked, reverse_revoked_to_disabled),
        migrations.AlterField(
            model_name="connectorinstallation",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("active", "Active"),
                    ("expired", "Expired"),
                    ("revoked", "Revoked"),
                    ("error", "Error"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="ConnectorCredential",
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
                    "auth_type",
                    models.CharField(
                        choices=[("api_key", "API Key"), ("oauth2", "OAuth 2.0")],
                        db_index=True,
                        default="oauth2",
                        max_length=16,
                    ),
                ),
                ("encrypted_access_token", models.TextField(blank=True, default="")),
                ("encrypted_refresh_token", models.TextField(blank=True, default="")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "connector_installation",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credential",
                        to="turing.connectorinstallation",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="connector_credentials",
                        to="turing.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Connector credential",
                "verbose_name_plural": "Connector credentials",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="connectorcredential",
            index=models.Index(
                fields=["organization", "auth_type"],
                name="turing_conncred_org_auth",
            ),
        ),
    ]
