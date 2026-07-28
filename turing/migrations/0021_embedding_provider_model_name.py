# Phase 4.5.5 — Embedding.provider / model_name

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("turing", "0020_pgvector_embedding_dimensions"),
    ]

    operations = [
        migrations.AddField(
            model_name="embedding",
            name="provider",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="EmbeddingProvider code (e.g. local, null).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="embedding",
            name="model_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Embedding model identifier.",
                max_length=128,
            ),
        ),
    ]
