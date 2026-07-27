from __future__ import annotations

from typing import Any

from turing.conf import get_turing_settings
from turing.providers.base import STTProvider
from turing.providers.speechmatics.client import SpeechmaticsClient
from turing.providers.speechmatics.mapper import map_speechmatics_transcript
from turing.providers.types import (
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
    TranscriptionRequest,
)


class SpeechmaticsAdapter(STTProvider):
    """Speechmatics Batch API adapter implementing the STT provider port."""

    code = "speechmatics"
    display_name = "Speechmatics"

    def __init__(self, client: SpeechmaticsClient | None = None) -> None:
        self._client = client

    def _get_client(self) -> SpeechmaticsClient:
        if self._client is not None:
            return self._client
        settings = get_turing_settings()
        # Priority: DB encrypted secret → env → empty (client errors clearly)
        api_key = settings.speechmatics_api_key
        base_url = settings.speechmatics_base_url
        operating_point = "enhanced"
        try:
            from turing.models.configuration import SpeechProviderConfig

            row = SpeechProviderConfig.objects.filter(code=self.code, is_active=True).first()
            if row:
                db_key = (row.api_key or "").strip()
                if db_key:
                    api_key = db_key
                base_url = row.base_url or base_url
                operating_point = row.operating_point or operating_point
        except Exception:
            pass
        self._operating_point_default = operating_point
        self._client = SpeechmaticsClient(api_key=api_key, base_url=base_url)
        return self._client

    def submit(self, request: TranscriptionRequest) -> ProviderJobHandle:
        client = self._get_client()
        config = self._build_config(request)
        response = client.submit_job(
            config=config,
            media_url=request.media_url if not request.media_bytes else None,
            media_bytes=request.media_bytes,
            filename=request.filename,
            content_type=request.content_type,
        )
        job_payload = response.get("job") or response
        external_id = str(job_payload.get("id") or response.get("id") or "")
        return ProviderJobHandle(
            external_job_id=external_id,
            provider_code=self.code,
            metadata={"raw": response},
        )

    def get_status(self, handle: ProviderJobHandle) -> ProviderJobStatus:
        client = self._get_client()
        response = client.get_job(handle.external_job_id)
        job = response.get("job") or response
        status = str(job.get("status") or "").lower()
        mapped = {
            "running": "running",
            "done": "succeeded",
            "rejected": "failed",
            "deleted": "failed",
            "expired": "failed",
        }.get(status, "running" if status else "running")
        return ProviderJobStatus(
            external_job_id=handle.external_job_id,
            state=mapped,
            message=str(job.get("errors") or job.get("status") or ""),
            raw=response,
        )

    def fetch_result(self, handle: ProviderJobHandle) -> NormalizedTranscript:
        client = self._get_client()
        payload = client.get_transcript(handle.external_job_id)
        return map_speechmatics_transcript(payload)

    def cancel(self, handle: ProviderJobHandle) -> None:
        client = self._get_client()
        client.delete_job(handle.external_job_id)

    def _build_config(self, request: TranscriptionRequest) -> dict[str, Any]:
        operating_point = request.operating_point or getattr(
            self, "_operating_point_default", "enhanced"
        )
        transcription_config: dict[str, Any] = {
            "operating_point": operating_point,
            "diarization": "speaker" if request.diarization else "none",
        }
        language = (request.language_code or "").strip() or (
            str(request.extra_options.get("language") or "").strip()
        )
        if not language:
            from turing.domain.exceptions import ConfigurationError

            raise ConfigurationError(
                "Speechmatics requires a language_code (e.g. fa, en). "
                "Set it on the ProcessingJob or Platform configuration default language."
            )
        transcription_config["language"] = language

        config: dict[str, Any] = {
            "type": "transcription",
            "transcription_config": transcription_config,
        }
        if request.media_url:
            config["fetch_data"] = {"url": request.media_url}
        # Merge provider/job extras carefully
        for key, value in (request.extra_options or {}).items():
            if key == "language":
                continue  # already applied
            elif key == "transcription_config" and isinstance(value, dict):
                transcription_config.update(value)
                # Do not allow extras to wipe required language
                transcription_config["language"] = language
            else:
                config[key] = value
        self._maybe_add_notification_config(config)
        return config

    def _maybe_add_notification_config(self, config: dict[str, Any]) -> None:
        settings = get_turing_settings()
        if settings.webhook_mode != "augment":
            return
        secret = (settings.speechmatics_webhook_secret or "").strip()
        callback = (settings.webhook_callback_url or "").strip()
        if not secret or not callback:
            return
        from turing.providers.speechmatics.webhook import notification_config_for_submit

        config["notification_config"] = notification_config_for_submit(
            callback_url=callback,
            bearer_secret=secret,
        )
