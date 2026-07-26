from __future__ import annotations

from typing import Any

from turing.providers.types import (
    NormalizedSegment,
    NormalizedSpeaker,
    NormalizedTranscript,
    NormalizedWord,
)


def map_speechmatics_transcript(payload: dict[str, Any]) -> NormalizedTranscript:
    """
    Map Speechmatics json-v2 transcript into the Turing normalized DTO.
    """
    results = payload.get("results") or []
    metadata = payload.get("metadata") or {}
    language = (
        metadata.get("language_pack_info", {}).get("language_code")
        or metadata.get("language")
        or ""
    )

    speakers_map: dict[str, NormalizedSpeaker] = {}
    segments: list[NormalizedSegment] = []
    current_words: list[NormalizedWord] = []
    current_speaker: str | None = None
    current_start: int | None = None
    current_end: int = 0
    confidences: list[float] = []
    sequence = 0

    def flush() -> None:
        nonlocal sequence, current_words, current_speaker, current_start, current_end
        if not current_words:
            return
        text = _words_to_text(current_words)
        word_conf = [w.confidence for w in current_words if w.confidence is not None]
        seg_conf = sum(word_conf) / len(word_conf) if word_conf else None
        if current_speaker and current_speaker not in speakers_map:
            speakers_map[current_speaker] = NormalizedSpeaker(
                label=current_speaker,
                display_name=current_speaker,
            )
        segments.append(
            NormalizedSegment(
                sequence=sequence,
                text=text,
                start_ms=current_start or 0,
                end_ms=current_end,
                confidence=seg_conf,
                speaker_label=current_speaker,
                words=list(current_words),
            )
        )
        sequence += 1
        current_words = []
        current_start = None

    for item in results:
        item_type = item.get("type")
        alternatives = item.get("alternatives") or []
        alt = alternatives[0] if alternatives else {}
        content = alt.get("content") or ""
        speaker = alt.get("speaker") or item.get("speaker")
        conf = alt.get("confidence")
        start_ms = _sec_to_ms(item.get("start_time"))
        end_ms = _sec_to_ms(item.get("end_time"))

        if item_type == "word":
            if speaker and current_speaker and speaker != current_speaker and current_words:
                flush()
            if current_start is None:
                current_start = start_ms
            current_end = end_ms
            current_speaker = speaker or current_speaker
            if conf is not None:
                confidences.append(float(conf))
            current_words.append(
                NormalizedWord(
                    text=content,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=float(conf) if conf is not None else None,
                    speaker_label=speaker,
                )
            )
        elif item_type == "punctuation":
            if current_words:
                # Attach punctuation to previous word text stream via pseudo-word
                current_words.append(
                    NormalizedWord(
                        text=content,
                        start_ms=current_end,
                        end_ms=current_end,
                        confidence=None,
                        speaker_label=current_speaker,
                    )
                )
                if item.get("is_eos"):
                    flush()
        # Ignore entity / other types for Phase 1

    flush()

    full_text = "\n".join(
        f"{s.speaker_label + ': ' if s.speaker_label else ''}{s.text}".strip()
        for s in segments
    )
    confidence_avg = sum(confidences) / len(confidences) if confidences else None

    return NormalizedTranscript(
        language_code=str(language or ""),
        full_text=full_text,
        confidence_avg=confidence_avg,
        speakers=list(speakers_map.values()),
        segments=segments,
        raw={"metadata": metadata},
    )


def _sec_to_ms(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(float(value) * 1000)
    except (TypeError, ValueError):
        return 0


def _words_to_text(words: list[NormalizedWord]) -> str:
    parts: list[str] = []
    for word in words:
        token = word.text
        if not parts:
            parts.append(token)
            continue
        if token in {".", ",", "!", "?", ";", ":", "%"} or token.startswith("'"):
            parts[-1] = parts[-1] + token
        else:
            parts.append(token)
    return " ".join(parts)
