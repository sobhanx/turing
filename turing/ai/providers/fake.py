from __future__ import annotations

from turing.ai.interfaces import AIProvider
from turing.ai.registry import AIProviderRegistry
from turing.ai.types import AnalysisResult, TranscriptInput


@AIProviderRegistry.register
class FakeAIProvider(AIProvider):
    """Deterministic provider for tests and local development."""

    code = "fake"
    model_name = "fake-v1"

    def summarize(self, transcript: TranscriptInput) -> AnalysisResult:
        preview = (transcript.full_text or "").strip()[:120]
        return AnalysisResult(
            content={
                "summary": f"Summary of transcript {transcript.transcript_id}: {preview}",
                "main_points": _main_points_from_text(transcript.full_text),
            },
            model_name=self.model_name,
        )

    def extract_action_items(self, transcript: TranscriptInput) -> AnalysisResult:
        items = [
            {
                "task": f"Follow up on transcript {transcript.transcript_id}",
                "owner": None,
                "deadline": None,
            }
        ]
        for segment in transcript.segments[:2]:
            text = segment.text.strip()
            if text:
                items.append({"task": f"Review: {text[:80]}", "owner": None, "deadline": None})
        return AnalysisResult(content=items, model_name=self.model_name)

    def extract_topics(self, transcript: TranscriptInput) -> AnalysisResult:
        topics = _topics_from_text(transcript.full_text)
        return AnalysisResult(content=topics, model_name=self.model_name)


def _main_points_from_text(text: str) -> list[str]:
    body = (text or "").strip()
    if not body:
        return []
    chunks = [part.strip() for part in body.replace("\n", " ").split(".") if part.strip()]
    return chunks[:3] or [body[:80]]


def _topics_from_text(text: str) -> list[str]:
    words = [w.lower() for w in (text or "").split() if len(w) > 4]
    if not words:
        return ["general"]
    unique = []
    for word in words:
        if word not in unique:
            unique.append(word)
        if len(unique) >= 5:
            break
    return unique or ["general"]
