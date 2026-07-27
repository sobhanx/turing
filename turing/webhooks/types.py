from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderNotification:
    """Normalized provider webhook payload for async processing."""

    provider_code: str
    external_job_id: str
    status_param: str
    provider_state: str
    provider_message: str
    dedupe_key: str
    payload_hash: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderNotification:
        return cls(
            provider_code=str(data["provider_code"]),
            external_job_id=str(data["external_job_id"]),
            status_param=str(data.get("status_param") or ""),
            provider_state=str(data["provider_state"]),
            provider_message=str(data.get("provider_message") or ""),
            dedupe_key=str(data["dedupe_key"]),
            payload_hash=str(data.get("payload_hash") or ""),
            raw_metadata=dict(data.get("raw_metadata") or {}),
        )
