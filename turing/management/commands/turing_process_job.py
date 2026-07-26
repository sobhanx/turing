from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from turing.services.transcription import TranscriptionService


class Command(BaseCommand):
    help = (
        "Process a Turing transcription job synchronously "
        "(useful when Celery is not running)."
    )

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=str, help="ProcessingJob UUID")

    def handle(self, *args, **options):
        job_id = options["job_id"]
        self.stdout.write(f"Processing job {job_id}…")
        try:
            transcript = TranscriptionService().process_job(job_id)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Transcript {transcript.id} status={transcript.status}"
            )
        )
