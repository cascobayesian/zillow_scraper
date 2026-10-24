"""Proxy pool loading and rotation."""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_SCHEME = "http"
HOST_PORT_USER_PASS_PARTS = 4


@dataclass(frozen=True)
class Proxy:
    """A single proxy server, with optional credentials."""

    server: str
    username: str | None = None
    password: str | None = None

    def to_playwright(self) -> dict[str, str]:
        """Convert to the dict format Playwright/Patchright expects."""
        settings = {"server": self.server}
        if self.username:
            settings["username"] = self.username
        if self.password:
            settings["password"] = self.password
        return settings

    def __str__(self) -> str:
        """Show the proxy without its password."""
        return f"{self.username}@{self.server}" if self.username else self.server


def parse_proxy(line: str) -> Proxy:
    """
    Parse a proxy from one line of text.

    Accepted formats:
        scheme://user:pass@host:port
        scheme://host:port
        host:port
        host:port:user:pass
    """
    text = line.strip()
    if not text:
        msg = "Empty proxy line"
        raise ValueError(msg)

    if "://" not in text:
        parts = text.split(":")
        if len(parts) == HOST_PORT_USER_PASS_PARTS:
            host, port, username, password = parts
            return Proxy(f"{DEFAULT_SCHEME}://{host}:{port}", username, password)
        text = f"{DEFAULT_SCHEME}://{text}"

    parsed = urlsplit(text)
    if not parsed.hostname or not parsed.port:
        msg = f"Proxy must include a host and port: {line!r}"
        raise ValueError(msg)

    return Proxy(f"{parsed.scheme}://{parsed.hostname}:{parsed.port}", parsed.username, parsed.password)


def load_proxies(proxy_file: Path) -> list[Proxy]:
    """Load proxies from a file, one per line. Blank lines and lines starting with '#' are ignored."""
    if not proxy_file.exists():
        logger.warning("Proxy file %s not found, connecting directly (no proxy rotation)", proxy_file)
        return []

    proxies = []
    for line_number, line in enumerate(proxy_file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            proxies.append(parse_proxy(line))
        except ValueError as e:
            logger.error("Skipping proxy on line %s of %s: %s", line_number, proxy_file, e)

    logger.info("Loaded %s proxies from %s", len(proxies), proxy_file)
    return proxies


class NoProxiesAvailableError(Exception):
    """Raised when every configured proxy is cooling down after failures."""


class ProxyPool:
    """Round-robin proxy rotation that rests proxies after they fail."""

    def __init__(self, proxies: list[Proxy], cooldown_seconds: float = 900) -> None:
        self.proxies = proxies
        self.cooldown_seconds = cooldown_seconds
        self._index = 0
        self._failed_at: dict[Proxy, float] = {}

    def _is_available(self, proxy: Proxy) -> bool:
        failed_at = self._failed_at.get(proxy)
        return failed_at is None or time.monotonic() - failed_at >= self.cooldown_seconds

    def next(self) -> Proxy | None:
        """
        Return the next available proxy in rotation.

        Returns None when no proxies are configured (direct connection).
        Raises NoProxiesAvailableError when proxies are configured but all are cooling down.
        """
        if not self.proxies:
            return None

        for _ in range(len(self.proxies)):
            proxy = self.proxies[self._index]
            self._index = (self._index + 1) % len(self.proxies)
            if self._is_available(proxy):
                return proxy

        msg = f"All {len(self.proxies)} proxies failed within the last {self.cooldown_seconds:.0f}s"
        raise NoProxiesAvailableError(msg)

    def mark_failed(self, proxy: Proxy | None) -> None:
        """Rest a proxy for the cooldown period."""
        if proxy is None:
            return
        logger.warning("Resting proxy %s for %.0fs after failure", proxy, self.cooldown_seconds)
        self._failed_at[proxy] = time.monotonic()

    def mark_succeeded(self, proxy: Proxy | None) -> None:
        """Clear any failure record for a proxy."""
        if proxy is not None:
            self._failed_at.pop(proxy, None)
