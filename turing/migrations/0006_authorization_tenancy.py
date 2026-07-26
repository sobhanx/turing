# Generated manually for Phase 2.7 Authorization & Tenancy

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_default_org_and_backfill(apps, schema_editor):
    Organization = apps.get_model("turing", "Organization")
    TuringMembership = apps.get_model("turing", "TuringMembership")
    MediaAsset = apps.get_model("turing", "MediaAsset")
    ProcessingJob = apps.get_model("turing", "ProcessingJob")
    Transcript = apps.get_model("turing", "Transcript")

    org, _ = Organization.objects.get_or_create(
        slug="default",
        defaults={
            "name": "Default",
            "external_key": "",
            "is_active": True,
            "notes": "Seeded default organization for local/demo use.",
        },
    )

    TuringMembership.objects.filter(organization_id__isnull=True).update(organization_id=org.pk)
    MediaAsset.objects.filter(organization_id__isnull=True).update(organization_id=org.pk)
    ProcessingJob.objects.filter(organization_id__isnull=True).update(organization_id=org.pk)
    Transcript.objects.filter(organization_id__isnull=True).update(organization_id=org.pk)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("turing", "0005_transcript_intelligence"),
    ]

    operations = [
        migrations.CreateModel(
            name="Organization",
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
                ("name", models.CharField(max_length=128)),
                ("slug", models.SlugField(max_length=64, unique=True)),
                (
                    "external_key",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        default="",
                        help_text="Optional host-project key (mirrors historical tenant_key).",
                        max_length=64,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.CharField(blank=True, default="", max_length=255)),
            ],
            options={
                "verbose_name": "Organization",
                "verbose_name_plural": "Organizations",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Owning organization (data boundary). Defaults to the seeded Default org.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="media_assets",
                to="turing.organization",
            ),
        ),
        migrations.AddField(
            model_name="processingjob",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Copied from media at job creation.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="jobs",
                to="turing.organization",
            ),
        ),
        migrations.AddField(
            model_name="transcript",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                help_text="Copied from job/media at persist time.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transcripts",
                to="turing.organization",
            ),
        ),
        # Membership: add nullable organization first, then alter user OneToOne → FK
        migrations.AddField(
            model_name="turingmembership",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="memberships",
                to="turing.organization",
            ),
        ),
        migrations.RunPython(seed_default_org_and_backfill, noop_reverse),
        migrations.AlterField(
            model_name="turingmembership",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="turing_memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="turingmembership",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="memberships",
                to="turing.organization",
            ),
        ),
        migrations.AlterModelOptions(
            name="turingmembership",
            options={
                "verbose_name": "Turing membership",
                "verbose_name_plural": "Turing memberships",
            },
        ),
        migrations.AddConstraint(
            model_name="turingmembership",
            constraint=models.UniqueConstraint(
                fields=("user", "organization"),
                name="turing_membership_user_org_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="turingmembership",
            index=models.Index(
                fields=["organization", "role"],
                name="turing_turi_organiz_7a8c01_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="turingmembership",
            index=models.Index(
                fields=["user", "is_active"],
                name="turing_turi_user_id_9f2c11_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mediaasset",
            index=models.Index(
                fields=["organization", "-created_at"],
                name="turing_medi_organiz_a1b2c3_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="processingjob",
            index=models.Index(
                fields=["organization", "-created_at"],
                name="turing_proc_organiz_d4e5f6_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="transcript",
            index=models.Index(
                fields=["organization", "status"],
                name="turing_tran_organiz_g7h8i9_idx",
            ),
        ),
        migrations.AlterField(
            model_name="mediaasset",
            name="tenant_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Optional host-project isolation key (often mirrors organization.slug).",
                max_length=64,
            ),
        ),
    ]
