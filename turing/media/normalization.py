from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from turing.domain.ingestion import (
    CANONICAL_CHANNELS,
    CANONICAL_CODEC,
    CANONICAL_FORMAT,
    CANONICAL_SAMPLE_RATE_HZ,
    AudioProbeResult,
)
from turing.media.inspection import AudioInspectionService

logger = logging.getLogger(__name__)

DEFAULT_FFMPEG_TIMEOUT_SECONDS = 600


def resolve_ffmpeg_path() -> str | None:
    configured = (os.environ.get("FFMPEG_PATH") or "").strip()
    if configured and shutil.which(configured):
        return configured
    return shutil.which("ffmpeg")


@dataclass(frozen=True)
class NormalizationResult:
    output_path: str
    probe: AudioProbeResult
    success: bool
    error_message: str = ""


class AudioNormalizationService:
    """Normalize audio to canonical STT input via ffmpeg."""

    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        inspector: AudioInspectionService | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path or resolve_ffmpeg_path()
        self.inspector = inspector or AudioInspectionService()
        self._runner = runner or subprocess.run

    def normalize(self, input_path: str, output_path: str) -> NormalizationResult:
        if not self.ffmpeg_path:
            logger.warning("ffmpeg not found; cannot normalize audio.")
            return NormalizationResult(
                output_path=output_path,
                probe=AudioProbeResult(
                    format="",
                    codec="",
                    duration_ms=None,
                    sample_rate_hz=None,
                    channels=None,
                    bitrate=None,
                    readable=False,
                    error_message="ffmpeg_not_available",
                ),
                success=False,
                error_message="ffmpeg_not_available",
            )

        command = [
            self.ffmpeg_path,
            "-y",
            "-i",
            input_path,
            "-ac",
            str(CANONICAL_CHANNELS),
            "-ar",
            str(CANONICAL_SAMPLE_RATE_HZ),
            "-c:a",
            CANONICAL_CODEC,
            "-f",
            CANONICAL_FORMAT,
            output_path,
        ]
        try:
            completed = self._runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=DEFAULT_FFMPEG_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("ffmpeg failed for %s: %s", input_path, exc)
            return NormalizationResult(
                output_path=output_path,
                probe=AudioProbeResult(
                    format="",
                    codec="",
                    duration_ms=None,
                    sample_rate_hz=None,
                    channels=None,
                    bitrate=None,
                    readable=False,
                    error_message=str(exc),
                ),
                success=False,
                error_message=str(exc),
            )

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            logger.warning("ffmpeg returned %s: %s", completed.returncode, stderr)
            return NormalizationResult(
                output_path=output_path,
                probe=AudioProbeResult(
                    format="",
                    codec="",
                    duration_ms=None,
                    sample_rate_hz=None,
                    channels=None,
                    bitrate=None,
                    readable=False,
                    error_message=stderr or "ffmpeg_failed",
                ),
                success=False,
                error_message=stderr or "ffmpeg_failed",
            )

        probe = self.inspector.probe(output_path)
        return NormalizationResult(
            output_path=output_path,
            probe=probe,
            success=probe.readable,
            error_message="" if probe.readable else probe.error_message,
        )
