from django.db import migrations, models

import turing.security.fields


def encrypt_plaintext_api_keys(apps, schema_editor):
    SpeechProviderConfig = apps.get_model("turing", "SpeechProviderConfig")
    from turing.security.secrets import encrypt_secret, is_encrypted

    for row in SpeechProviderConfig.objects.all().iterator():
        raw = row.api_key or ""
        if raw and not is_encrypted(raw):
            SpeechProviderConfig.objects.filter(pk=row.pk).update(api_key=encrypt_secret(raw))


def noop_reverse(apps, schema_editor):
    # Keep encrypted values; decryption still works for app reads.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("turing", "0002_language_help_text"),
    ]

    operations = [
        migrations.AlterField(
            model_name="speechproviderconfig",
            name="api_key",
            field=turing.security.fields.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "Provider API key (stored encrypted). Leave blank in Admin to keep the "
                    "current key, or clear via env-only setup. Empty falls back to "
                    "TURING_SPEECHMATICS_API_KEY."
                ),
                max_length=1024,
            ),
        ),
        migrations.RunPython(encrypt_plaintext_api_keys, noop_reverse),
    ]
