from __future__ import annotations

from abc import ABC, abstractmethod

from turing.providers.types import (
    NormalizedTranscript,
    ProviderJobHandle,
    ProviderJobStatus,
    TranscriptionRequest,
)


class STTProvider(ABC):
    """
    Port for speech-to-text providers.

    Core services depend on this interface only — never on Speechmatics types.
    """

    code: str
    display_name: str

    @abstractmethod
    def submit(self, request: TranscriptionRequest) -> ProviderJobHandle:
        """Start transcription (async batch)."""

    @abstractmethod
    def get_status(self, handle: ProviderJobHandle) -> ProviderJobStatus:
        """Poll provider job status."""

    @abstractmethod
    def fetch_result(self, handle: ProviderJobHandle) -> NormalizedTranscript:
        """Fetch and normalize the completed transcript."""

    def cancel(self, handle: ProviderJobHandle) -> None:
        """Best-effort cancel; default is no-op."""
        return None
