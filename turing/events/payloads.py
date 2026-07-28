from __future__ import annotations

from uuid import UUID

from turing.models import ExternalReference


def snapshot_external_references(
    *,
    organization_id: int | None,
    media_id: UUID | str | None = None,
    transcript_id: UUID | str | None = None,
) -> list[dict[str, str]]:
    """
    Minimal host-link snapshot for event payloads (ids only, no transcript text).
    """
    if organization_id is None:
        return []

    qs = ExternalReference.objects.filter(organization_id=organization_id)
    if media_id is not None:
        qs = qs.filter(media_id=media_id)
    elif transcript_id is not None:
        qs = qs.filter(transcript_id=transcript_id)
    else:
        return []

    return [
        {
            "external_system": row.external_system,
            "external_type": row.external_type,
            "external_id": row.external_id,
        }
        for row in qs.only("external_system", "external_type", "external_id")
    ]
