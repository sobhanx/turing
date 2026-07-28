# Phase 4.3.1 — connector installation + sync job foundation

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0015_outbox_reliability"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConnectorInstallation",
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
                    "connector_type",
                    models.CharField(
                        db_index=True,
                        help_text=(
                            "Registry key (e.g. zoom, crm). "
                            "Must match a registered connector."
                        ),
                        max_length=64,
                    ),
                ),
                ("name", models.CharField(max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("disabled", "Disabled"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=16,
                    ),
                ),
                (
                    "config",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Connector-specific settings (secrets filtered in Admin/API)."
                        ),
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="connector_installations",
                        to="turing.organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Connector installation",
                "verbose_name_plural": "Connector installations",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ConnectorSyncJob",
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
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("records_processed", models.PositiveIntegerField(default=0)),
                ("error", models.TextField(blank=True, default="")),
                (
                    "installation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sync_jobs",
                        to="turing.connectorinstallation",
                    ),
                ),
            ],
            options={
                "verbose_name": "Connector sync job",
                "verbose_name_plural": "Connector sync jobs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="connectorinstallation",
            index=models.Index(
                fields=["organization", "connector_type"],
                name="turing_conn_org_type",
            ),
        ),
        migrations.AddIndex(
            model_name="connectorinstallation",
            index=models.Index(
                fields=["organization", "status"],
                name="turing_conn_org_status",
            ),
        ),
        migrations.AddConstraint(
            model_name="connectorinstallation",
            constraint=models.UniqueConstraint(
                fields=("organization", "name"),
                name="turing_connector_org_name_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="connectorsyncjob",
            index=models.Index(
                fields=["installation", "-created_at"],
                name="turing_connsync_inst",
            ),
        ),
        migrations.AddIndex(
            model_name="connectorsyncjob",
            index=models.Index(
                fields=["status", "-created_at"],
                name="turing_connsync_status",
            ),
        ),
    ]
