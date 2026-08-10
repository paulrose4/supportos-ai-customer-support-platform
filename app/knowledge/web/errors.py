class UnusableWebContentError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.replace("_", " "))


class RetryableWebFetchError(ConnectionError):
    def __init__(self, status_code: int, retry_after_seconds: int | None = None) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        detail = f"page returned HTTP {status_code}"
        if retry_after_seconds is not None:
            detail += f"; retry after {retry_after_seconds} seconds"
        super().__init__(detail)


class StagingCleanupRequiredError(RuntimeError):
    """A failed page left an artifact store needing explicit remediation."""
