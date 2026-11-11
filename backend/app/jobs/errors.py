class PermanentError(Exception):
    """Retrying cannot succeed. The job goes straight to the dead-letter queue."""


class RetryableError(Exception):
    """Transient failure (scanner or AI provider down, network). The job is retried with backoff."""
