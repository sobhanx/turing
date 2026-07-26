from __future__ import annotations


class TuringError(Exception):
    """Base domain error."""

    code: str = "turing_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.message = message


class ValidationError(TuringError):
    code = "validation_error"


class NotFoundError(TuringError):
    code = "not_found"


class ProviderError(TuringError):
    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.retryable = retryable
        self.provider_code = provider_code


class JobStateError(TuringError):
    code = "job_state_error"


class ConfigurationError(TuringError):
    code = "configuration_error"


class PermissionDeniedError(TuringError):
    code = "permission_denied"
