# Generated manually for Phase A stabilization

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0022_speaker_label_name"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="processingjob",
            name="turing_job_idempotency_key_uniq",
        ),
        migrations.AddConstraint(
            model_name="processingjob",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("organization", "idempotency_key"),
                name="turing_job_org_idempotency_key_uniq",
            ),
        ),
    ]
