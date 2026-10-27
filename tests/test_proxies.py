"""Tests for proxies.py."""

from pathlib import Path

import pytest

from src.proxies import NoProxiesAvailableError, Proxy, ProxyPool, load_proxies, parse_proxy


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("http://user:pass@1.2.3.4:8080", Proxy("http://1.2.3.4:8080", "user", "pass")),
        ("socks5://1.2.3.4:1080", Proxy("socks5://1.2.3.4:1080")),
        ("1.2.3.4:8080", Proxy("http://1.2.3.4:8080")),
        ("proxy.example.com:8080:user:pass", Proxy("http://proxy.example.com:8080", "user", "pass")),
        ("  1.2.3.4:8080  ", Proxy("http://1.2.3.4:8080")),
    ],
)
def test_parse_proxy(line: str, expected: Proxy) -> None:
    assert parse_proxy(line) == expected


@pytest.mark.parametrize("line", ["", "1.2.3.4", "http://1.2.3.4"])
def test_parse_proxy_invalid(line: str) -> None:
    with pytest.raises(ValueError, match=r"(?i)proxy"):
        parse_proxy(line)


def test_to_playwright_and_str_hide_password() -> None:
    proxy = Proxy("http://1.2.3.4:8080", "user", "secret")
    assert proxy.to_playwright() == {"server": "http://1.2.3.4:8080", "username": "user", "password": "secret"}
    assert "secret" not in str(proxy)
    assert Proxy("http://1.2.3.4:8080").to_playwright() == {"server": "http://1.2.3.4:8080"}


def test_load_proxies_skips_comments_and_bad_lines(tmp_path: Path) -> None:
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("# comment\n\n1.2.3.4:8080\nnot-a-proxy\nhttp://u:p@5.6.7.8:3128\n", encoding="utf-8")
    assert load_proxies(proxy_file) == [Proxy("http://1.2.3.4:8080"), Proxy("http://5.6.7.8:3128", "u", "p")]


def test_load_proxies_missing_file(tmp_path: Path) -> None:
    assert load_proxies(tmp_path / "missing.txt") == []


def test_pool_round_robin() -> None:
    proxies = [Proxy("http://a:1"), Proxy("http://b:2")]
    pool = ProxyPool(proxies)
    assert [pool.next() for _ in range(4)] == [proxies[0], proxies[1], proxies[0], proxies[1]]


def test_pool_empty_means_direct() -> None:
    assert ProxyPool([]).next() is None


def test_pool_skips_failed_until_cooldown_expires() -> None:
    proxies = [Proxy("http://a:1"), Proxy("http://b:2")]
    pool = ProxyPool(proxies, cooldown_seconds=0)
    pool.mark_failed(proxies[0])
    assert pool.next() == proxies[0]  # zero cooldown: immediately available again

    pool = ProxyPool(proxies, cooldown_seconds=3600)
    pool.mark_failed(proxies[0])
    assert [pool.next() for _ in range(3)] == [proxies[1]] * 3

    pool.mark_succeeded(proxies[0])
    assert proxies[0] in {pool.next(), pool.next()}


def test_pool_all_failed_raises() -> None:
    proxies = [Proxy("http://a:1")]
    pool = ProxyPool(proxies, cooldown_seconds=3600)
    pool.mark_failed(proxies[0])
    with pytest.raises(NoProxiesAvailableError):
        pool.next()
