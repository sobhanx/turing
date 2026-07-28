# Phase 4.5.4 — pgvector-compatible Embedding.dimensions

from django.db import migrations, models


def _try_create_vector_extension(apps, schema_editor):
    """Best-effort CREATE EXTENSION vector on PostgreSQL (no-op elsewhere)."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:  # noqa: BLE001
            # Extension may require superuser; JSON vectors still work.
            pass


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0019_semantic_search_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="embedding",
            name="dimensions",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Embedding dimensionality (0 = unset / null provider).",
            ),
        ),
        migrations.AlterField(
            model_name="embedding",
            name="vector",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="pgvector-compatible embedding (list of floats).",
            ),
        ),
        migrations.RunPython(
            _try_create_vector_extension,
            migrations.RunPython.noop,
        ),
    ]
