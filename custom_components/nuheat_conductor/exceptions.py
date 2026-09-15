"""Exceptions for the Nuheat OpenAPI client."""


class NuheatError(Exception):
    """Base error for the Nuheat OpenAPI."""


class NuheatAuthError(NuheatError):
    """Credentials were rejected; the user must sign in again."""


class NuheatRateLimitedError(NuheatError):
    """The API returned 429."""

    def __init__(self, retry_after: int | None) -> None:
        """Initialize with the server's Retry-After, if any."""
        super().__init__(f"Rate limited (retry after {retry_after}s)")
        self.retry_after = retry_after


class NuheatApiError(NuheatError):
    """Any other API or transport failure."""
