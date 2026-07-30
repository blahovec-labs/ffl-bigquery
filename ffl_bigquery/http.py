"""ThrottledSession: polite HTTP for third-party ADP sources.

FFC's terms ask callers not to poll frequently (their data updates once daily) and
MFL rejects requests without a real User-Agent. The backfill issues ~10**3 calls,
so spacing and jitter are correctness requirements, not niceties.
"""
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import requests

log = logging.getLogger(__name__)

_TLS_HINT = (
    "TLS verification failed. A local security product is likely intercepting HTTPS. "
    "`--native-tls`/`--with truststore` alone will NOT fix this -- those only make the "
    "`truststore` package available, they don't activate it. You must call "
    "`truststore.inject_into_ssl()` in-process before making requests, e.g. "
    "`python -c \"import truststore; truststore.inject_into_ssl(); ...\"`. "
    "(This repo's test suite does this automatically for network-marked tests via "
    "tests/conftest.py; see README.md.)"
)


class SourceUnavailable(RuntimeError):  # noqa: N818 - API contract name
    """A third-party source could not be reached after retries."""


class ThrottledSession:
    def __init__(
        self,
        *,
        user_agent: str,
        min_interval: float = 1.0,
        max_retries: int = 4,
        backoff: float = 2.0,
        jitter: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
        session: Any | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff = backoff
        self.jitter = jitter
        self._sleep = sleep
        self._session = session or requests.Session()
        self.timeout = timeout
        self._last_call: float | None = None

    def _throttle(self) -> None:
        if self._last_call is None:
            return
        wait = self.min_interval + random.uniform(0.0, self.jitter)
        self._sleep(wait)

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            self._last_call = time.monotonic()
            try:
                resp = self._session.get(
                    url,
                    params=params,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:  # noqa: BLE001 - retry any transient transport error
                last = e
                if "certificate verify failed" in str(e):
                    raise SourceUnavailable(f"{_TLS_HINT} (original: {e})") from e
                wait = self.backoff * (2 ** (attempt - 1)) + random.uniform(0.0, self.jitter)
                log.warning(
                    "GET %s failed (attempt %d/%d): %s; retrying in %.1fs",
                    url, attempt, self.max_retries, e, wait,
                )
                if attempt < self.max_retries:
                    self._sleep(wait)
        raise SourceUnavailable(
            f"GET {url} failed after {self.max_retries} attempts"
        ) from last
