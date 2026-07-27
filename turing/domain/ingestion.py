from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AudioProbeResult:
    """Structured ffprobe output for the primary audio stream."""

    format: str
    codec: str
    duration_ms: int | None
    sample_rate_hz: int | None
    channels: int | None
    bitrate: int | None
    raw: dict[str, Any] = field(default_factory=dict)
    readable: bool = True
    error_message: str = ""


# Canonical STT input target (Phase 3.3).
CANONICAL_FORMAT = "wav"
CANONICAL_CODEC = "pcm_s16le"
CANONICAL_SAMPLE_RATE_HZ = 16_000
CANONICAL_CHANNELS = 1


def is_stt_compatible(probe: AudioProbeResult) -> bool:
    """Return True when probe indicates audio already matches the canonical target."""
    if not probe.readable:
        return False
    fmt = (probe.format or "").lower()
    codec = _normalize_codec(probe.codec)
    if fmt not in {CANONICAL_FORMAT, "wave"}:
        return False
    if codec not in {"pcm_s16le", "pcm_s16", "s16", "pcm_s16be"}:
        return False
    if probe.sample_rate_hz != CANONICAL_SAMPLE_RATE_HZ:
        return False
    if probe.channels != CANONICAL_CHANNELS:
        return False
    return True


def needs_normalization(probe: AudioProbeResult) -> bool:
    if not probe.readable:
        return False
    return not is_stt_compatible(probe)


def _normalize_codec(codec: str) -> str:
    value = (codec or "").lower().strip()
    aliases = {
        "pcm_s16le": "pcm_s16le",
        "pcm_s16": "pcm_s16le",
        "s16le": "pcm_s16le",
        "s16": "pcm_s16le",
    }
    return aliases.get(value, value)
