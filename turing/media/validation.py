"""Audio upload validation (extensions, MIME types, size)."""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

from turing.conf import get_turing_settings
from turing.domain.exceptions import ValidationError

# Defaults — overridable via Platform configuration / env
DEFAULT_AUDIO_EXTENSIONS: frozenset[str] = frozenset({"mp3", "wav", "m4a", "webm", "ogg"})
DEFAULT_AUDIO_MIME_TYPES: frozenset[str] = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/webm",
        "audio/ogg",
        "audio/vorbis",
        "application/ogg",
        "video/webm",  # webm audio-only containers often report this
    }
)

EXTENSION_MIME_HINTS: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "webm": "audio/webm",
    "ogg": "audio/ogg",
}


def _parse_csv_set(raw: str, *, default: frozenset[str]) -> frozenset[str]:
    items = {part.strip().lower().lstrip(".") for part in (raw or "").split(",") if part.strip()}
    return frozenset(items) if items else default


def allowed_extensions() -> frozenset[str]:
    settings = get_turing_settings()
    return _parse_csv_set(settings.allowed_audio_extensions, default=DEFAULT_AUDIO_EXTENSIONS)


def allowed_mime_types() -> frozenset[str]:
    settings = get_turing_settings()
    return _parse_csv_set(settings.allowed_audio_mime_types, default=DEFAULT_AUDIO_MIME_TYPES)


def normalize_extension(filename: str) -> str:
    return PurePosixPath(filename or "").suffix.lower().lstrip(".")


def resolve_content_type(filename: str, content_type: str = "") -> str:
    if content_type and content_type != "application/octet-stream":
        return content_type.split(";")[0].strip().lower()
    ext = normalize_extension(filename)
    if ext in EXTENSION_MIME_HINTS:
        return EXTENSION_MIME_HINTS[ext]
    guessed = mimetypes.guess_type(filename)[0]
    return (guessed or "application/octet-stream").lower()


def validate_audio_upload(
    *,
    filename: str,
    content_type: str = "",
    byte_size: int,
) -> tuple[str, str]:
    """
    Validate upload constraints.

    Returns ``(normalized_extension, resolved_content_type)``.
    """
    settings = get_turing_settings()
    if byte_size <= 0:
        raise ValidationError("Uploaded file is empty.")
    if byte_size > settings.max_upload_bytes:
        raise ValidationError(
            f"File exceeds max upload size of {settings.max_upload_bytes} bytes "
            f"({byte_size} bytes received)."
        )

    ext = normalize_extension(filename)
    allowed_ext = allowed_extensions()
    if not ext or ext not in allowed_ext:
        raise ValidationError(
            f"Unsupported audio file extension '{ext or '(none)'}'. "
            f"Allowed: {', '.join(sorted(allowed_ext))}."
        )

    resolved_type = resolve_content_type(filename, content_type)
    allowed_mime = allowed_mime_types()
    # Allow octet-stream when extension is trusted (browsers sometimes omit MIME)
    if resolved_type != "application/octet-stream" and resolved_type not in allowed_mime:
        raise ValidationError(
            f"Unsupported audio MIME type '{resolved_type}'. "
            f"Allowed types include: {', '.join(sorted(allowed_mime))}."
        )

    return ext, resolved_type if resolved_type != "application/octet-stream" else EXTENSION_MIME_HINTS.get(
        ext, resolved_type
    )
