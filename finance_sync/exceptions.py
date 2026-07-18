"""Error hierarchy for the synchronization layer.

The engine's retry policy keys off :class:`TransientSyncError` — anything that
subclasses it is retried with exponential backoff; everything else fails the
connection's sync immediately (but never the whole run).
"""


class SyncError(Exception):
    """Base class for all synchronization errors."""

    #: machine-readable slug stored in the sync_errors table
    error_type: str = "sync_error"


class ConfigurationError(SyncError):
    """Adapter or connection is misconfigured (missing credentials, bad slug)."""

    error_type = "configuration"


class AuthenticationError(SyncError):
    """Credentials rejected by the provider."""

    error_type = "authentication"


class TokenExpiredError(AuthenticationError):
    """Access token expired and could not be refreshed."""

    error_type = "token_expired"


class DataValidationError(SyncError):
    """Provider data failed canonical validation and was rejected."""

    error_type = "validation"


class UnsupportedInstitutionError(SyncError):
    """Requested institution has no registered adapter or no live API."""

    error_type = "unsupported"


class TransientSyncError(SyncError):
    """Recoverable failure — the engine retries these with backoff."""

    error_type = "transient"


class NetworkError(TransientSyncError):
    """Connection failure / timeout while talking to the provider."""

    error_type = "network"


class RateLimitError(TransientSyncError):
    """Provider throttled the request (HTTP 429)."""

    error_type = "rate_limit"

    def __init__(self, message: str = "Rate limited", retry_after: float = 1.0):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderOutageError(TransientSyncError):
    """Provider returned a 5xx / is unavailable."""

    error_type = "provider_outage"
