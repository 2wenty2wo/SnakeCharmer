import logging
import time

import requests

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class RetryClient:
    """Base HTTP client with exponential backoff retry on transient failures.

    Subclasses should set ``_service_name`` for log messages and may override
    ``_handle_rate_limit`` to add service-specific rate-limit handling.
    """

    _service_name: str = "API"

    def __init__(
        self,
        session: requests.Session,
        base_url: str,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ):
        self.session = session
        self.base_url = base_url
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    def close(self) -> None:
        """Close the underlying requests session."""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
            log.warning(
                "Error while closing %s session (suppressing; original exception pending)",
                self._service_name,
                exc_info=True,
            )
        return None

    def _handle_rate_limit(
        self, resp: requests.Response, method: str, url: str, **kwargs
    ) -> requests.Response | None:
        """Hook for subclass rate-limit handling.

        Return a new response to use in place of *resp*, or ``None`` to skip.
        """
        return None

    def _on_connection_exhausted(self, exc: requests.ConnectionError) -> None:
        """Hook called when all connection retries are exhausted."""

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make an HTTP request with retry on transient failures."""
        url = f"{self.base_url}{path}"
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        last_exception: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)

                # Let subclasses handle rate limiting
                replacement = self._handle_rate_limit(resp, method, url, **kwargs)
                if replacement is not None:
                    resp = replacement

                if resp.status_code >= 500 and attempt < self.max_retries:
                    delay = self.retry_backoff ** (attempt + 1)
                    log.warning(
                        "%s returned %d, retrying in %.1fs (attempt %d/%d)",
                        self._service_name,
                        resp.status_code,
                        delay,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(delay)
                    continue

                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.retry_backoff ** (attempt + 1)
                    log.warning(
                        "%s request failed (%s), retrying in %.1fs (attempt %d/%d)",
                        self._service_name,
                        type(e).__name__,
                        delay,
                        attempt + 1,
                        self.max_retries,
                    )
                    time.sleep(delay)
                    continue
                if isinstance(e, requests.ConnectionError):
                    self._on_connection_exhausted(e)
                raise

        if last_exception is not None:
            raise last_exception
        # All retry attempts exhausted with 5xx responses
        resp.raise_for_status()  # type: ignore[possibly-undefined]
        return resp  # type: ignore[possibly-undefined]
