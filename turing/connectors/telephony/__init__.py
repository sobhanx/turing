from __future__ import annotations

"""Generic telephony connector foundation (Phase 4.4.3)."""

from turing.connectors.telephony.connector import TelephonyConnector
from turing.connectors.telephony.serializers import (
    DEFAULT_EXTERNAL_SYSTEM,
    EXTERNAL_TYPE_CALL,
    TelephonyCall,
    normalize_call,
    normalize_calls,
)

__all__ = [
    "DEFAULT_EXTERNAL_SYSTEM",
    "EXTERNAL_TYPE_CALL",
    "TelephonyCall",
    "TelephonyConnector",
    "normalize_call",
    "normalize_calls",
]
