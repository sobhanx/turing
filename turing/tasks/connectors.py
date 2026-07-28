from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="turing.tasks.connectors.sync_connector_installation",
    acks_late=True,
    max_retries=0,
)
def sync_connector_installation(self, sync_job_id: str) -> str:
    """Execute a pending ``ConnectorSyncJob`` via ``ConnectorSyncService``."""
    from turing.domain.exceptions import NotFoundError, TuringError
    from turing.services.connector_sync import ConnectorSyncService

    service = ConnectorSyncService()
    try:
        job = service.run_sync(sync_job_id)
    except NotFoundError:
        logger.warning("Connector sync job %s not found", sync_job_id)
        raise
    except TuringError:
        logger.exception("Connector sync aborted for job %s", sync_job_id)
        raise
    except Exception:
        logger.exception("Connector sync unexpected error for job %s", sync_job_id)
        raise
    return f"{job.status}:{job.records_processed}"
