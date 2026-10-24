"""Main entry point for Zillow scraper."""

import asyncio
import csv
import logging
from dataclasses import asdict, fields

from patchright.async_api import BrowserContext
from patchright.async_api import Error as PlaywrightError

from src.automation import (
    ScrapeProgress,
    create_browser_context,
    deduplicate_listings,
    get_browser_page,
    scrape_pages,
)
from src.config import Config, ScraperSettings, SubmissionType, load_configs, load_settings
from src.constants import CaptchaDetectedError, ZillowParseError
from src.form_submission import submit_listings
from src.proxies import ProxyPool, load_proxies
from src.scraper import PropertyListing
from src.sheets_submission import SheetsSubmitter
from src.throttle import RequestThrottle

logging.basicConfig(level=logging.INFO, format="%(levelname)s:zillow_scraper:%(name)s:%(message)s")
logger = logging.getLogger(__name__)


async def scrape_listings(config: Config, pool: ProxyPool, throttle: RequestThrottle, settings: ScraperSettings) -> list[PropertyListing]:
    """
    Scrape and deduplicate listings from Zillow, rotating proxies as it goes.

    Each proxy gets a fresh browser profile and scrapes up to settings.pages_per_proxy pages
    before the next proxy picks up at the following page. On a CAPTCHA or browser error the
    failing proxy is rested, the scraper backs off, and a different proxy retries the same page.
    """
    progress = ScrapeProgress(next_url=config.search_url)
    consecutive_failures = 0

    while progress.next_url is not None:
        proxy = pool.next()
        try:
            async with create_browser_context(proxy) as context, get_browser_page(context) as page:
                await scrape_pages(page, progress, throttle, settings.pages_per_proxy)
        except ZillowParseError as e:
            # The page loaded but its layout wasn't recognized; retrying through another proxy won't help.
            logger.error("Could not parse %s, stopping with %s listings: %s", progress.next_url, len(progress.listings), e)
            break
        except (CaptchaDetectedError, PlaywrightError, TimeoutError) as e:
            consecutive_failures += 1
            logger.warning("Proxy %s failed on %s: %s", proxy or "direct", progress.next_url, e)
            pool.mark_failed(proxy)
            if consecutive_failures >= settings.max_consecutive_failures:
                logger.error("Giving up after %s consecutive failures; keeping %s listings scraped so far", consecutive_failures, len(progress.listings))
                break
            await throttle.backoff(consecutive_failures)
            continue

        pool.mark_succeeded(proxy)
        consecutive_failures = 0

    logger.info("Deduplicating %s listings from %s page(s)...", len(progress.listings), progress.pages_scraped)
    return deduplicate_listings(progress.listings)


def write_csv(config: Config, listings: list[PropertyListing]) -> None:
    """Write listings to the config's CSV file."""
    if config.csv_path is None:
        return
    config.csv_path.parent.mkdir(parents=True, exist_ok=True)
    with config.csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[field.name for field in fields(PropertyListing)])
        writer.writeheader()
        writer.writerows(asdict(listing) for listing in listings)
    logger.info("Wrote %s listings to %s", len(listings), config.csv_path)


async def submit_listings_to_destination(context: BrowserContext | None, config: Config, listings: list[PropertyListing]) -> None:
    """Submit listings based on configuration."""
    if not listings:
        logger.warning("No listings to submit")
        return

    if config.submission_type == SubmissionType.SHEET and isinstance(config.sheet_url, str):
        logger.info("Submitting %s listings to Google Sheets...", len(listings))
        submitter = SheetsSubmitter()
        submitter.submit_listings(
            listings=listings,
            sheet_url=config.sheet_url,
            worksheet_name=config.sheet_name,
        )
    elif config.submission_type == SubmissionType.FORM and isinstance(config.form_url, str) and context is not None:
        logger.info("Submitting %s listings to Google Form...", len(listings))
        async with get_browser_page(context) as page:
            await submit_listings(page, config.form_url, listings)
    else:
        logger.warning("No submission destination configured")


async def scrape_and_submit(config: Config, pool: ProxyPool, throttle: RequestThrottle, settings: ScraperSettings) -> None:
    """Orchestrate scraping and submission workflow."""
    listings = await scrape_listings(config, pool, throttle, settings)
    write_csv(config, listings)
    if config.submission_type == SubmissionType.FORM:
        async with create_browser_context() as context:
            await submit_listings_to_destination(context, config, listings)
    elif config.submission_type == SubmissionType.SHEET:
        await submit_listings_to_destination(None, config, listings)


async def configure_and_run() -> None:
    """Load configurations and run scraper for each."""
    settings = load_settings()
    configs = load_configs()
    pool = ProxyPool(load_proxies(settings.proxy_file), cooldown_seconds=settings.proxy_cooldown_seconds)
    throttle = RequestThrottle(
        min_delay=settings.min_delay_seconds,
        max_delay=settings.max_delay_seconds,
        backoff_base=settings.backoff_base_seconds,
        backoff_max=settings.backoff_max_seconds,
    )
    for config in configs:
        logger.info("Processing config: '%s'", config.config_name)
        await scrape_and_submit(config, pool, throttle, settings)
        logger.debug("Completed config: '%s'", config.config_name)


def main() -> None:
    """Call configure_and_run via asyncio.run."""
    asyncio.run(configure_and_run())


if __name__ == "__main__":
    main()
