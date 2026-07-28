# Phase 4.3.7 — connector credential lifecycle fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0017_connector_oauth_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="connectorcredential",
            name="last_refreshed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="connectorcredential",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
