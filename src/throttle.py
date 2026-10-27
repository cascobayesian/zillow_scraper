"""Request pacing so the scraper queries Zillow slowly."""

import asyncio
import logging
import time
from random import SystemRandom

logger = logging.getLogger(__name__)
cryptogen = SystemRandom()


class RequestThrottle:
    """
    Enforce a randomized minimum gap between page requests.

    The gap is shared across all browser contexts and proxies, so rotating proxies
    never increases the overall request rate against the site.
    """

    def __init__(self, min_delay: float, max_delay: float, backoff_base: float, backoff_max: float) -> None:
        if min_delay < 0 or max_delay < min_delay:
            msg = f"Invalid delay range: {min_delay}-{max_delay}"
            raise ValueError(msg)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._last_request: float | None = None

    async def wait(self) -> None:
        """Sleep until enough time has passed since the previous request, then record this one."""
        if self._last_request is not None:
            gap = cryptogen.uniform(self.min_delay, self.max_delay)
            remaining = gap - (time.monotonic() - self._last_request)
            if remaining > 0:
                logger.info("Pacing requests: waiting %.0fs before next page load", remaining)
                await asyncio.sleep(remaining)
        self._last_request = time.monotonic()

    async def backoff(self, consecutive_failures: int) -> None:
        """Sleep with exponential backoff after a block or failure."""
        delay = min(self.backoff_base * 2 ** (consecutive_failures - 1), self.backoff_max)
        delay *= cryptogen.uniform(0.8, 1.2)
        logger.warning("Backing off for %.0fs after %s consecutive failure(s)", delay, consecutive_failures)
        await asyncio.sleep(delay)
