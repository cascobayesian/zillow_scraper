"""Browser automation, configuration, and page processing."""

import logging
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from random import SystemRandom
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from patchright.async_api import BrowserContext, Page, async_playwright

from src.constants import (
    MAX_SCROLL_DOWN,
    MAX_SCROLL_UP,
    MAX_WAIT_TIME,
    MIN_SCROLL_DOWN,
    MIN_SCROLL_UP,
    MIN_WAIT_TIME,
    PROBABILITY_SCROLL_UP,
    CaptchaDetectedError,
    ZillowParseError,
)
from src.scraper import PropertyListing, ZillowHomeFinder, ZillowSearchJson

if TYPE_CHECKING:
    from src.proxies import Proxy
    from src.throttle import RequestThrottle

logger = logging.getLogger(__name__)
cryptogen = SystemRandom()


@asynccontextmanager
async def create_browser_context(proxy: "Proxy | None" = None) -> AsyncGenerator[BrowserContext, Any]:
    """Create and configure browser with Patchright's stealth mode, optionally routed through a proxy."""
    with tempfile.TemporaryDirectory(prefix="patchright_") as temp_dir:
        async with async_playwright() as p:
            logger.info("Launching browser via %s", proxy or "direct connection")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=temp_dir,
                channel="chrome",
                headless=False,
                no_viewport=True,
                proxy=proxy.to_playwright() if proxy else None,  # type: ignore[arg-type]
            )

            try:
                yield context
            finally:
                await context.close()


@asynccontextmanager
async def get_browser_page(context: BrowserContext, *, require_new_page: bool = False) -> AsyncGenerator[Page, Any]:
    """Create a browser page ready to use."""
    # Reuse existing page if available, otherwise create new one
    pages = context.pages
    if pages and not require_new_page:
        page = pages[0]
    else:
        page = await context.new_page()

    try:
        yield page
    finally:
        await context.new_page()
        await page.close()


# Browser Automation - Scrolling and Navigation


async def close_modal_if_present(page: Page) -> None:
    """Close modal dialog."""
    try:
        await page.wait_for_load_state()
        close_button = page.locator("button[data-c11n-component='Modal.CloseButton']").first
        is_visible = await close_button.is_visible()
        if is_visible:
            logger.debug("Popup modal detected, attempting to close it")
            await close_button.click()
            return
        logger.warning("Invisible CloseButton modal found")
    except TimeoutError as e:
        logger.debug("No CloseButton modal found or could not close: %s", e)


async def get_property_card_count(page: Page) -> int:
    """Get the current count of loaded property cards."""
    cards = await page.query_selector_all('article[data-test="property-card"]')
    return len(cards)


async def is_bottom_element_visible(page: Page) -> bool:
    """Check if the bottom element is visible in the viewport."""
    bottom_element = await page.query_selector("div.search-list-save-search-parent")
    if not bottom_element:
        return False

    return await page.evaluate(
        """
        (element) => {
            const rect = element.getBoundingClientRect();
            return rect.top < window.innerHeight && rect.bottom > 0;
        }
        """,
        bottom_element,
    )


async def scroll_page(page: Page, amount: int) -> None:
    """Scroll down by the specified amount, falling back to window scroll if needed."""
    result = await page.evaluate(
        f"""
        (() => {{
            const beforeScroll = window.scrollY;
            window.scrollBy(0, {amount});
            const afterScroll = window.scrollY;
            return {{method: 'window', before: beforeScroll, after: afterScroll}};
        }})()
        """
    )
    logger.debug("Scroll result: %s", result)


async def simulate_human_behavior(page: Page) -> None:
    """Simulate human-like mouse movements and pauses."""
    window_dimensions = await page.evaluate("""
        () => ({
            width: window.innerWidth,
            height: window.innerHeight
        })
    """)

    # Move mouse to random positions within the window
    x = cryptogen.randint(100, window_dimensions["width"] - 100)
    y = cryptogen.randint(100, window_dimensions["height"] - 100)
    await page.mouse.move(x, y)

    await page.wait_for_timeout(cryptogen.randint(MIN_WAIT_TIME, MAX_WAIT_TIME))


async def perform_human_like_scroll(page: Page) -> None:
    """Perform a human-like scrolling action with random variations."""
    scroll_amount = cryptogen.randint(MIN_SCROLL_DOWN, MAX_SCROLL_DOWN)
    await scroll_page(page, scroll_amount)
    await simulate_human_behavior(page)

    # Occasionally scroll back up
    if cryptogen.random() < PROBABILITY_SCROLL_UP:
        back_scroll = cryptogen.randint(MIN_SCROLL_UP, MAX_SCROLL_UP)
        await scroll_page(page, -back_scroll)
        await simulate_human_behavior(page)


async def scroll_to_top(page: Page) -> None:
    """Scroll back to the top of the page."""
    await page.evaluate("""
        window.scrollTo(0, 0);
    """)
    await simulate_human_behavior(page)


async def scroll_and_load_listings(page: Page, max_entries: int = 100, max_no_change: int = 3, max_scroll_attempts: int = 50) -> None:
    """Scroll through search results to trigger lazy loading."""
    await page.wait_for_selector('[id="grid-search-results"]', timeout=10000)

    previous_count = 0
    no_change_iterations = 0

    for iteration in range(max_scroll_attempts):
        current_count = await get_property_card_count(page)
        logger.debug("Iteration %s: Found %s property cards", iteration + 1, current_count)

        # Check stopping conditions
        if current_count >= max_entries:
            logger.info("Reached target of %s entries", max_entries)
            break

        if await is_bottom_element_visible(page):
            logger.debug("Reached bottom of page (search-list-save-search-parent element is visible)")
            break

        # Track stagnation
        if current_count == previous_count:
            no_change_iterations += 1
            if no_change_iterations >= max_no_change:
                logger.warning("No new content loaded after %s attempts, stopping", iteration)
                break
        else:
            no_change_iterations = 0

        previous_count = current_count

        # Perform scrolling action
        await perform_human_like_scroll(page)

    final_count = await get_property_card_count(page)
    logger.debug("Lazy loading complete. Total property cards loaded: %s", final_count)

    await scroll_to_top(page)
    await simulate_human_behavior(page)


async def raise_if_captcha(page: Page) -> None:
    """Raise CaptchaDetectedError if Zillow served its bot check instead of results."""
    if await page.get_by_text("Press & Hold").count() > 0 or await page.locator("#px-captcha").count() > 0:
        error_msg = f"CAPTCHA detected at {page.url}"
        raise CaptchaDetectedError(error_msg)


async def get_next_page_url(page: Page) -> str | None:
    """Return the absolute URL of the next results page, or None on the last page."""
    next_button = page.locator("a[title='Next page']").first
    if await next_button.count() == 0:
        logger.debug("No next page button found")
        return None

    if await next_button.get_attribute("aria-disabled") == "true" or await next_button.is_disabled():
        logger.debug("Next page button is disabled")
        return None

    href = await next_button.get_attribute("href")
    if not href:
        logger.debug("Next page button has no href")
        return None

    return urljoin(page.url, href)


async def sort_by_newest(page: Page) -> None:
    """Sort listings by newest first. Leaves the default order if the sort controls aren't found."""
    for selector in ("button[aria-label='Sort Properties']", "button[id='sort-popover']"):
        sort_button = page.locator(selector).first
        if await sort_button.count() > 0:
            break
        logger.debug("Sort button not found: %s", selector)
    else:
        logger.warning("No sort button found, keeping Zillow's default order")
        return

    await sort_button.click()
    await page.wait_for_load_state()
    await simulate_human_behavior(page)

    newest_button = page.get_by_text("Newest").first
    if await newest_button.count() == 0:
        logger.warning("No sort by newest option found, keeping Zillow's default order")
        return

    await newest_button.click()
    await page.wait_for_load_state()


# Page Processing


async def scrape_single_page(page: Page) -> list[PropertyListing]:
    """Scrape listings from a single page."""
    await scroll_and_load_listings(page)

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")

    finder = ZillowHomeFinder(soup)
    return finder.listings


async def read_search_json(page: Page) -> ZillowSearchJson | None:
    """Parse the search-results JSON embedded in the page, or None if it's missing or malformed."""
    next_data = await page.evaluate("() => document.getElementById('__NEXT_DATA__')?.textContent ?? null")
    if not next_data:
        logger.warning("No embedded search JSON on page")
        return None
    try:
        return ZillowSearchJson(next_data)
    except ZillowParseError as e:
        logger.warning("%s", e)
        return None


@dataclass
class ScrapeProgress:
    """Where a multi-proxy scrape has got to, so a new proxy can resume from the same page."""

    next_url: str | None
    listings: list[PropertyListing] = field(default_factory=list)
    pages_scraped: int = 0
    sorted_by_newest: bool = False


async def load_results_page(page: Page, url: str, throttle: "RequestThrottle") -> None:
    """Navigate to a results page after waiting for the throttle, and check for a bot block."""
    await throttle.wait()
    logger.info("Loading %s", url)
    await page.goto(url)
    await simulate_human_behavior(page)
    await raise_if_captcha(page)
    await close_modal_if_present(page)


async def scrape_pages(page: Page, progress: ScrapeProgress, throttle: "RequestThrottle", max_pages: int) -> None:
    """
    Scrape up to max_pages result pages starting at progress.next_url, updating progress in place.

    progress is updated after every page, so if this raises partway through,
    the caller can resume from progress.next_url with a different proxy.
    """
    for _ in range(max_pages):
        if progress.next_url is None:
            return

        await load_results_page(page, progress.next_url, throttle)

        if not progress.sorted_by_newest:
            await throttle.wait()
            await sort_by_newest(page)
            await simulate_human_behavior(page)
            await raise_if_captcha(page)
            progress.sorted_by_newest = True

        search_json = await read_search_json(page)
        if search_json is not None:
            page_listings = search_json.listings
            progress.next_url = search_json.next_url
        else:
            logger.info("Falling back to parsing listing cards from HTML")
            page_listings = await scrape_single_page(page)
            progress.next_url = await get_next_page_url(page)

        progress.listings.extend(page_listings)
        progress.pages_scraped += 1
        logger.info("Page %s: %s listings (%s total)", progress.pages_scraped, len(page_listings), len(progress.listings))


# Data Processing


def deduplicate_listings(listings: list[PropertyListing]) -> list[PropertyListing]:
    """Deduplicate listings based on unique combination of address, price, and link."""
    seen = set()
    unique_listings = []
    duplicates_removed = 0

    for listing in listings:
        # Create a unique key from the listing
        key = (listing.address, listing.price, listing.link)

        if key not in seen:
            seen.add(key)
            unique_listings.append(listing)
        else:
            duplicates_removed += 1

    if duplicates_removed > 0:
        logger.debug("Removed %s duplicate listings", duplicates_removed)

    logger.debug("Unique listings after deduplication: %s", len(unique_listings))
    return unique_listings
