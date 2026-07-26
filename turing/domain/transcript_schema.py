"""Provider-agnostic transcript word / segment schemas."""

from __future__ import annotations

from typing import Any

from turing.providers.types import NormalizedWord


WORD_SCHEMA_KEYS = ("text", "start_ms", "end_ms", "confidence")


def normalize_word_dict(payload: dict[str, Any] | NormalizedWord) -> dict[str, Any]:
    """
    Map provider-specific or NormalizedWord payloads into the shared word schema.

    Output always contains: text, start_ms, end_ms, confidence.
    Extra keys (e.g. speaker_label) are preserved for forward compatibility.
    """
    if isinstance(payload, NormalizedWord):
        data = {
            "text": payload.text,
            "start_ms": int(payload.start_ms or 0),
            "end_ms": int(payload.end_ms or 0),
            "confidence": payload.confidence,
            "speaker_label": payload.speaker_label,
        }
        return {k: v for k, v in data.items() if v is not None or k in WORD_SCHEMA_KEYS}

    text = payload.get("text") or payload.get("content") or payload.get("word") or ""
    if "start_ms" in payload:
        start_ms = int(float(payload.get("start_ms") or 0))
    else:
        start_ms = _coerce_time_to_ms(
            payload.get("start", payload.get("start_time", 0)),
            from_seconds_keys=True,
        )
    if "end_ms" in payload:
        end_ms = int(float(payload.get("end_ms") or 0))
    else:
        end_ms = _coerce_time_to_ms(
            payload.get("end", payload.get("end_time", 0)),
            from_seconds_keys=True,
        )
    conf = payload.get("confidence", payload.get("score"))

    try:
        confidence = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        confidence = None

    result: dict[str, Any] = {
        "text": str(text),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": confidence,
    }
    for key, value in payload.items():
        if key not in result and key not in {
            "content",
            "word",
            "start",
            "end",
            "start_time",
            "end_time",
            "score",
        }:
            result[key] = value
    return result


def words_to_json_list(words: list[NormalizedWord] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_word_dict(w) for w in words]


def count_words_in_segments(segments) -> int:
    total = 0
    for seg in segments:
        words = getattr(seg, "words", None) or []
        if words:
            total += len(words)
        elif getattr(seg, "text", None):
            total += len(str(seg.text).split())
    return total


def _coerce_time_to_ms(value: Any, *, from_seconds_keys: bool = False) -> int:
    if value is None:
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if from_seconds_keys and number < 10_000:
        return int(number * 1000)
    return int(number)
