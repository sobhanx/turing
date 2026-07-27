from __future__ import annotations

from abc import ABC, abstractmethod

from turing.ai.types import AnalysisResult, TranscriptInput
from turing.domain.enums import AnalysisType


class AIProvider(ABC):
    """Strategy interface for transcript intelligence tasks."""

    code: str

    @abstractmethod
    def summarize(self, transcript: TranscriptInput) -> AnalysisResult:
        """Return summary + main points."""

    @abstractmethod
    def extract_action_items(self, transcript: TranscriptInput) -> AnalysisResult:
        """Return structured action items."""

    @abstractmethod
    def extract_topics(self, transcript: TranscriptInput) -> AnalysisResult:
        """Return topic labels."""

    def analyze(self, transcript: TranscriptInput, analysis_type: str) -> AnalysisResult:
        if analysis_type == AnalysisType.SUMMARY:
            return self.summarize(transcript)
        if analysis_type == AnalysisType.ACTION_ITEMS:
            return self.extract_action_items(transcript)
        if analysis_type == AnalysisType.TOPICS:
            return self.extract_topics(transcript)
        raise ValueError(f"Unsupported analysis type: {analysis_type}")
