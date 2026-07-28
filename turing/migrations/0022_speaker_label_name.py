# Speaker identity: label/display_name → speaker_label/speaker_name

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0021_embedding_provider_model_name"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="speaker",
            name="turing_speaker_transcript_label_uniq",
        ),
        migrations.RenameField(
            model_name="speaker",
            old_name="label",
            new_name="speaker_label",
        ),
        migrations.RenameField(
            model_name="speaker",
            old_name="display_name",
            new_name="speaker_name",
        ),
        migrations.AlterField(
            model_name="speaker",
            name="speaker_label",
            field=models.CharField(
                help_text="Immutable internal diarization identifier (e.g. S1).",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="speaker",
            name="speaker_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Editable display name. Empty → fall back to speaker_label.",
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="transcriptword",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Optional extras (speaker_label, speaker_name, provider ids, …)."
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="speaker",
            constraint=models.UniqueConstraint(
                fields=("transcript", "speaker_label"),
                name="turing_speaker_transcript_label_uniq",
            ),
        ),
    ]
