# Phase 2.9.2 — require organization FKs; UniqueConstraint replacements

import django.db.models.deletion
from django.db import migrations, models


def backfill_null_organizations(apps, schema_editor):
    Organization = apps.get_model("turing", "Organization")
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
    MediaAsset.objects.filter(organization_id__isnull=True).update(organization_id=org.pk)
    ProcessingJob.objects.filter(organization_id__isnull=True).update(organization_id=org.pk)
    # Prefer job org, then media org, then Default
    for transcript in Transcript.objects.filter(organization_id__isnull=True).iterator():
        org_id = None
        if transcript.job_id:
            job = ProcessingJob.objects.filter(pk=transcript.job_id).first()
            if job and job.organization_id:
                org_id = job.organization_id
        if org_id is None and transcript.media_id:
            media = MediaAsset.objects.filter(pk=transcript.media_id).first()
            if media and media.organization_id:
                org_id = media.organization_id
        transcript.organization_id = org_id or org.pk
        transcript.save(update_fields=["organization_id"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0006_authorization_tenancy"),
    ]

    operations = [
        migrations.RunPython(backfill_null_organizations, noop_reverse),
        migrations.AlterField(
            model_name="mediaasset",
            name="organization",
            field=models.ForeignKey(
                help_text="Owning organization (data boundary). Required.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="media_assets",
                to="turing.organization",
            ),
        ),
        migrations.AlterField(
            model_name="processingjob",
            name="organization",
            field=models.ForeignKey(
                help_text="Copied from media at job creation. Required.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="jobs",
                to="turing.organization",
            ),
        ),
        migrations.AlterField(
            model_name="transcript",
            name="organization",
            field=models.ForeignKey(
                help_text="Copied from job/media at persist time. Required.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transcripts",
                to="turing.organization",
            ),
        ),
        # unique_together → UniqueConstraint (same uniqueness semantics)
        migrations.AlterUniqueTogether(
            name="processingattempt",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="processingattempt",
            constraint=models.UniqueConstraint(
                fields=("job", "attempt_number"),
                name="turing_attempt_job_number_uniq",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="speaker",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="speaker",
            constraint=models.UniqueConstraint(
                fields=("transcript", "label"),
                name="turing_speaker_transcript_label_uniq",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="transcriptsegment",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="transcriptsegment",
            constraint=models.UniqueConstraint(
                fields=("transcript", "sequence"),
                name="turing_segment_transcript_sequence_uniq",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="transcriptword",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="transcriptword",
            constraint=models.UniqueConstraint(
                fields=("segment", "sequence"),
                name="turing_word_segment_sequence_uniq",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="transcriptrevision",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="transcriptrevision",
            constraint=models.UniqueConstraint(
                fields=("transcript", "revision_number"),
                name="turing_revision_transcript_number_uniq",
            ),
        ),
    ]
