from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from turing.connectors.exceptions import ConnectorError
from turing.connectors.registry import ConnectorRegistry
from turing.domain.enums import ConnectorInstallationStatus, ConnectorSyncJobStatus
from turing.domain.events import (
    connector_sync_completed,
    connector_sync_failed,
    connector_sync_started,
)
from turing.domain.exceptions import NotFoundError, ValidationError
from turing.events.bus import emit_after_commit
from turing.models import ConnectorInstallation, ConnectorSyncJob

logger = logging.getLogger(__name__)


class ConnectorSyncService:
    """Start and execute connector sync jobs (no vendor-specific logic)."""

    def start_sync(
        self,
        installation: ConnectorInstallation,
        *,
        auto_enqueue: bool = True,
    ) -> ConnectorSyncJob:
        if installation.status == ConnectorInstallationStatus.DISABLED:
            raise ValidationError("Cannot sync a disabled connector installation.")

        job = ConnectorSyncJob.objects.create(
            installation=installation,
            status=ConnectorSyncJobStatus.PENDING,
        )
        emit_after_commit(
            connector_sync_started(
                sync_job_id=str(job.id),
                installation_id=str(installation.id),
                organization_id=installation.organization_id,
                connector_type=installation.connector_type,
            )
        )
        if auto_enqueue:
            from turing.tasks.connectors import sync_connector_installation

            transaction.on_commit(
                lambda job_id=str(job.id): sync_connector_installation.delay(job_id)
            )
        return job

    def run_sync(self, job_id: str) -> ConnectorSyncJob:
        with transaction.atomic():
            try:
                job = (
                    ConnectorSyncJob.objects.select_for_update()
                    .select_related("installation", "installation__organization")
                    .get(pk=job_id)
                )
            except ConnectorSyncJob.DoesNotExist as exc:
                raise NotFoundError(f"Connector sync job '{job_id}' not found.") from exc

            if job.status == ConnectorSyncJobStatus.COMPLETED:
                return job
            if job.status == ConnectorSyncJobStatus.RUNNING:
                return job

            installation = job.installation
            if installation.status == ConnectorInstallationStatus.DISABLED:
                job.status = ConnectorSyncJobStatus.FAILED
                job.error = "Installation is disabled."
                job.started_at = timezone.now()
                job.finished_at = timezone.now()
                job.save(
                    update_fields=[
                        "status",
                        "error",
                        "started_at",
                        "finished_at",
                        "updated_at",
                    ]
                )
                self._emit_failed(job, installation)
                return job

            job.status = ConnectorSyncJobStatus.RUNNING
            job.started_at = timezone.now()
            job.error = ""
            job.save(update_fields=["status", "started_at", "error", "updated_at"])

        installation = job.installation
        try:
            connector = ConnectorRegistry.create(installation)
            connector.validate_config()
            result = connector.sync()
        except ConnectorError as exc:
            return self._fail(job, installation, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected connector sync failure for job %s", job_id)
            return self._fail(job, installation, f"Unexpected error: {exc}")

        with transaction.atomic():
            job = ConnectorSyncJob.objects.select_for_update().get(pk=job.pk)
            job.status = ConnectorSyncJobStatus.COMPLETED
            job.records_processed = int(result.records_processed or 0)
            job.finished_at = timezone.now()
            job.error = ""
            job.save(
                update_fields=[
                    "status",
                    "records_processed",
                    "finished_at",
                    "error",
                    "updated_at",
                ]
            )
            if installation.status == ConnectorInstallationStatus.ERROR:
                installation.status = ConnectorInstallationStatus.ACTIVE
                installation.save(update_fields=["status", "updated_at"])

        emit_after_commit(
            connector_sync_completed(
                sync_job_id=str(job.id),
                installation_id=str(installation.id),
                organization_id=installation.organization_id,
                connector_type=installation.connector_type,
                records_processed=job.records_processed,
            )
        )
        return job

    def _fail(
        self,
        job: ConnectorSyncJob,
        installation: ConnectorInstallation,
        message: str,
    ) -> ConnectorSyncJob:
        with transaction.atomic():
            job = ConnectorSyncJob.objects.select_for_update().get(pk=job.pk)
            job.status = ConnectorSyncJobStatus.FAILED
            job.error = (message or "")[:4000]
            job.finished_at = timezone.now()
            if job.started_at is None:
                job.started_at = job.finished_at
            job.save(
                update_fields=[
                    "status",
                    "error",
                    "finished_at",
                    "started_at",
                    "updated_at",
                ]
            )
            installation.status = ConnectorInstallationStatus.ERROR
            installation.save(update_fields=["status", "updated_at"])
        self._emit_failed(job, installation)
        return job

    def _emit_failed(
        self,
        job: ConnectorSyncJob,
        installation: ConnectorInstallation,
    ) -> None:
        emit_after_commit(
            connector_sync_failed(
                sync_job_id=str(job.id),
                installation_id=str(installation.id),
                organization_id=installation.organization_id,
                connector_type=installation.connector_type,
                error_code="connector_sync_failed",
            )
        )
