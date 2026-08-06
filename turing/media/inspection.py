from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Callable

from turing.domain.ingestion import AudioProbeResult

logger = logging.getLogger(__name__)

DEFAULT_FFPROBE_TIMEOUT_SECONDS = 30


def resolve_ffprobe_path() -> str | None:
    configured = (os.environ.get("FFPROBE_PATH") or "").strip()
    if configured and shutil.which(configured):
        return configured
    return shutil.which("ffprobe")


class AudioInspectionService:
    """Detect real audio properties via ffprobe (not extension/MIME)."""

    def __init__(
        self,
        *,
        ffprobe_path: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.ffprobe_path = ffprobe_path or resolve_ffprobe_path()
        self._runner = runner or subprocess.run

    def probe(self, path: str) -> AudioProbeResult:
        if not self.ffprobe_path:
            logger.warning("ffprobe not found; skipping audio inspection.")
            return AudioProbeResult(
                format="",
                codec="",
                duration_ms=None,
                sample_rate_hz=None,
                channels=None,
                bitrate=None,
                readable=False,
                error_message="ffprobe_not_available",
            )

        try:
            completed = self._runner(
                [
                    self.ffprobe_path,
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    path,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=DEFAULT_FFPROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("ffprobe failed for %s: %s", path, exc)
            return AudioProbeResult(
                format="",
                codec="",
                duration_ms=None,
                sample_rate_hz=None,
                channels=None,
                bitrate=None,
                readable=False,
                error_message=str(exc),
            )

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            logger.warning("ffprobe returned %s: %s", completed.returncode, stderr)
            return AudioProbeResult(
                format="",
                codec="",
                duration_ms=None,
                sample_rate_hz=None,
                channels=None,
                bitrate=None,
                readable=False,
                error_message=stderr or "ffprobe_failed",
            )

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            return AudioProbeResult(
                format="",
                codec="",
                duration_ms=None,
                sample_rate_hz=None,
                channels=None,
                bitrate=None,
                readable=False,
                error_message=f"invalid_ffprobe_json: {exc}",
            )

        return _parse_ffprobe_payload(payload)


def _parse_ffprobe_payload(payload: dict) -> AudioProbeResult:
    streams = payload.get("streams") or []
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    if audio_stream is None:
        return AudioProbeResult(
            format="",
            codec="",
            duration_ms=None,
            sample_rate_hz=None,
            channels=None,
            bitrate=None,
            raw=payload,
            readable=False,
            error_message="no_audio_stream",
        )

    fmt = payload.get("format") or {}
    duration_seconds = _coerce_float(fmt.get("duration"))
    if duration_seconds is None:
        duration_seconds = _coerce_float(audio_stream.get("duration"))

    bitrate = _coerce_int(fmt.get("bit_rate")) or _coerce_int(audio_stream.get("bit_rate"))
    sample_rate = _coerce_int(audio_stream.get("sample_rate"))
    channels = _coerce_int(audio_stream.get("channels"))
    codec = str(audio_stream.get("codec_name") or "")
    container = str(fmt.get("format_name") or "").split(",")[0]

    duration_ms = int(duration_seconds * 1000) if duration_seconds is not None else None

    return AudioProbeResult(
        format=container,
        codec=codec,
        duration_ms=duration_ms,
        sample_rate_hz=sample_rate,
        channels=channels,
        bitrate=bitrate,
        raw=payload,
        readable=True,
    )


def _coerce_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
