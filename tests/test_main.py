"""Tests for main.py."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _pytest.logging import LogCaptureFixture

from src.automation import ScrapeProgress
from src.config import Config, ScraperSettings, SubmissionType
from src.constants import CaptchaDetectedError
from src.main import configure_and_run, scrape_listings
from src.proxies import Proxy, ProxyPool
from src.scraper import PropertyListing


@pytest.mark.asyncio
async def test_main_with_mocked_configs(caplog: LogCaptureFixture) -> None:
    """Test that main() works with multiple configs."""
    mock_config = [
        Config(
            config_name="config1.env",
            search_url="https://zillow.com",
            submission_type=SubmissionType.FORM,
            form_url="https://form.com",
        ),
        Config(
            config_name="config2.env",
            search_url="https://zillow.com",
            submission_type=SubmissionType.SHEET,
            sheet_url="https://sheets.google.com/spreadsheet",
            sheet_name="Sheet1",
        ),
    ]

    with (
        patch("src.main.load_configs", return_value=mock_config),
        patch("src.main.scrape_and_submit", new_callable=AsyncMock) as mock_scrape_and_submit,
        caplog.at_level(logging.DEBUG),
    ):
        # Run the actual main function with await
        await configure_and_run()

        # Verify scrape_and_submit was called twice (once per config)
        assert mock_scrape_and_submit.call_count == 2

        # Verify it was called with the correct configs
        called_configs = [call.args[0] for call in mock_scrape_and_submit.call_args_list]
        assert called_configs == mock_config

        # Verify log messages
        assert "Processing config: 'config1.env'" in caplog.text
        assert "Completed config: 'config1.env'" in caplog.text
        assert "Processing config: 'config2.env'" in caplog.text
        assert "Completed config: 'config2.env'" in caplog.text


def _mock_context_manager() -> MagicMock:
    manager = MagicMock()
    manager.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    manager.return_value.__aexit__ = AsyncMock(return_value=None)
    return manager


@pytest.mark.asyncio
async def test_scrape_listings_rotates_proxy_each_page() -> None:
    """Each page is scraped through the next proxy, and a failing proxy is retried on the same page by another."""
    proxies = [Proxy("http://a:1"), Proxy("http://b:2"), Proxy("http://c:3")]
    pool = ProxyPool(proxies)
    settings = load_settings_for_test()
    throttle = MagicMock(wait=AsyncMock(), backoff=AsyncMock())
    config = Config(config_name="test", search_url="https://zillow.com/p1", submission_type=SubmissionType.NONE)

    pages = {
        "https://zillow.com/p1": ("https://zillow.com/p2", "1 Main St"),
        "https://zillow.com/p2": (None, "2 Main St"),
    }
    attempts: list[str] = []

    async def fake_scrape_pages(_page: MagicMock, progress: ScrapeProgress, _throttle: MagicMock, _max_pages: int) -> None:
        attempts.append(str(progress.next_url))
        if len(attempts) == 2:
            msg = "blocked"
            raise CaptchaDetectedError(msg)
        next_url, address = pages[str(progress.next_url)]
        progress.listings.append(PropertyListing(address, "$1", "1", str(progress.next_url)))
        progress.pages_scraped += 1
        progress.next_url = next_url

    browser = _mock_context_manager()
    with (
        patch("src.main.create_browser_context", browser),
        patch("src.main.get_browser_page", _mock_context_manager()),
        patch("src.main.scrape_pages", side_effect=fake_scrape_pages),
    ):
        listings = await scrape_listings(config, pool, throttle, settings)

    assert [listing.address for listing in listings] == ["1 Main St", "2 Main St"]
    assert attempts == ["https://zillow.com/p1", "https://zillow.com/p2", "https://zillow.com/p2"]
    assert [call.args[0] for call in browser.call_args_list] == proxies
    throttle.backoff.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_scrape_listings_gives_up_after_max_failures() -> None:
    """Scraping stops after max_consecutive_failures, keeping partial results."""
    settings = load_settings_for_test(max_consecutive_failures=2)
    throttle = MagicMock(wait=AsyncMock(), backoff=AsyncMock())
    config = Config(config_name="test", search_url="https://zillow.com/p1", submission_type=SubmissionType.NONE)

    with (
        patch("src.main.create_browser_context", _mock_context_manager()),
        patch("src.main.get_browser_page", _mock_context_manager()),
        patch("src.main.scrape_pages", side_effect=CaptchaDetectedError("blocked")) as mock_scrape,
    ):
        listings = await scrape_listings(config, ProxyPool([]), throttle, settings)

    assert listings == []
    assert mock_scrape.call_count == 2


def load_settings_for_test(**overrides: int) -> ScraperSettings:
    return ScraperSettings(**overrides)  # type: ignore[arg-type]
