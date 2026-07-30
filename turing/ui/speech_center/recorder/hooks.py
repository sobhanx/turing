"""Server-side config for the browser recorder (no upload logic)."""

from __future__ import annotations

from turing.conf import get_turing_settings


def recorder_client_config() -> dict:
    """
    Values injected into the upload page for MediaRecorder + upload UX.

    Recording still POSTs to the existing Speech Center upload endpoint and
    goes through ``MediaService.create_from_upload`` — same as file upload.
    """
    settings = get_turing_settings()
    return {
        "maxUploadBytes": int(settings.max_upload_bytes),
        "preferredMimeTypes": [
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/ogg;codecs=opus",
            "audio/ogg",
        ],
        "keyboard": {
            "toggle": "r",
            "pause": "p",
            "stop": "s",
            "delete": "Backspace",
        },
    }
