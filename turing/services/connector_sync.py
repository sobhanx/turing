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
        from turing.services.connector_installation import ConnectorInstallationService

        if not ConnectorInstallationService.is_syncable(installation):
            raise ValidationError(
                f"Cannot sync a connector installation in status "
                f"'{installation.status}'."
            )

        job = ConnectorSyncJob.objects.create(
            installation=installation,
            status=ConnectorSyncJobStatus.PENDING,
        )
        logger.info(
            "Connector sync started installation_id=%s connector_type=%s sync_job_id=%s",
            installation.id,
            installation.connector_type,
            job.id,
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

    def has_in_flight_sync(self, installation: ConnectorInstallation) -> bool:
        """True when a PENDING or RUNNING sync job exists for this installation."""
        return ConnectorSyncJob.objects.filter(
            installation_id=installation.pk,
            status__in=[
                ConnectorSyncJobStatus.PENDING,
                ConnectorSyncJobStatus.RUNNING,
            ],
        ).exists()

    def start_sync_if_idle(
        self,
        installation: ConnectorInstallation,
        *,
        auto_enqueue: bool = True,
    ) -> ConnectorSyncJob | None:
        """
        Start a sync only when no PENDING/RUNNING job exists for the installation.

        Uses a row lock so Beat and concurrent schedulers cannot double-enqueue.
        Failed jobs do not block; PENDING/EXPIRED/REVOKED installations are skipped.
        """
        from turing.services.connector_installation import ConnectorInstallationService

        with transaction.atomic():
            locked = (
                ConnectorInstallation.objects.select_for_update()
                .select_related("organization")
                .filter(pk=installation.pk)
                .first()
            )
            if locked is None:
                return None
            if not ConnectorInstallationService.is_syncable(locked):
                logger.info(
                    "Skipping non-syncable connector installation_id=%s "
                    "connector_type=%s status=%s",
                    locked.id,
                    locked.connector_type,
                    locked.status,
                )
                return None
            if self.has_in_flight_sync(locked):
                logger.info(
                    "Skipping in-flight connector sync installation_id=%s "
                    "connector_type=%s",
                    locked.id,
                    locked.connector_type,
                )
                return None
            return self.start_sync(locked, auto_enqueue=auto_enqueue)

    def discover_schedulable_installations(self):
        """
        Active (and recoverable ERROR) installations on active organizations.

        PENDING/EXPIRED/REVOKED are excluded. ERROR is included so a failed sync
        does not block the next scheduled run.
        """
        return (
            ConnectorInstallation.objects.filter(
                status__in=[
                    ConnectorInstallationStatus.ACTIVE,
                    ConnectorInstallationStatus.ERROR,
                ],
                organization__is_active=True,
            )
            .select_related("organization")
            .order_by("created_at")
        )

    def schedule_due_installations(self) -> dict[str, int]:
        """Discover installations and enqueue idle syncs (Celery Beat entrypoint)."""
        counts = {
            "examined": 0,
            "started": 0,
            "skipped_in_flight": 0,
            "errors": 0,
        }
        for installation in self.discover_schedulable_installations():
            counts["examined"] += 1
            try:
                job = self.start_sync_if_idle(installation, auto_enqueue=True)
                if job is None:
                    counts["skipped_in_flight"] += 1
                    continue
                counts["started"] += 1
                logger.info(
                    "Scheduled connector sync installation_id=%s connector_type=%s "
                    "sync_job_id=%s org_id=%s",
                    installation.id,
                    installation.connector_type,
                    job.id,
                    installation.organization_id,
                )
            except Exception:  # noqa: BLE001
                counts["errors"] += 1
                logger.exception(
                    "Failed to schedule connector sync installation_id=%s "
                    "connector_type=%s",
                    installation.id,
                    installation.connector_type,
                )
        return counts

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
            from turing.services.connector_installation import ConnectorInstallationService

            if not ConnectorInstallationService.is_syncable(installation):
                job.status = ConnectorSyncJobStatus.FAILED
                job.error = f"Installation is not syncable (status={installation.status})."
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
            connector.validate_credentials()
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
        logger.info(
            "Connector sync completed installation_id=%s connector_type=%s "
            "sync_job_id=%s records_processed=%s",
            installation.id,
            installation.connector_type,
            job.id,
            job.records_processed,
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
        logger.warning(
            "Connector sync failed installation_id=%s connector_type=%s "
            "sync_job_id=%s reason=%s",
            installation.id,
            installation.connector_type,
            job.id,
            (message or "")[:500],
        )
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
