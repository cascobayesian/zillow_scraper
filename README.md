[![Python checks](https://github.com/MaineDSA/zillow_scraper/actions/workflows/python.yml/badge.svg)](https://github.com/MaineDSA/zillow_scraper/actions/workflows/python.yml)
[![CodeQL analysis](https://github.com/MaineDSA/zillow_scraper/actions/workflows/codeql.yml/badge.svg)](https://github.com/MaineDSA/zillow_scraper/actions/workflows/codeql.yml)
[![Test coverage](https://raw.githubusercontent.com/MaineDSA/zillow_scraper/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/MaineDSA/zillow_scraper/blob/python-coverage-comment-action-data/htmlcov/index.html)


# Zillow Clone Scraper and Form Auto-Filler

This Python script scrapes apartment listings from a Zillow clone site and optionally submits the address, price, and link of each listing into a Google Form or Google Sheet.

## Features

- Scrapes addresses, prices, and links from listings
- Submits each listing to a Google Form or Google Sheet

## Requirements

- Python 3.11 or higher
- Linux, Windows, macOS

## How to Use

1. Clone the repository
1. Open terminal and navigate to the cloned directory
1. Install python dependencies

    ```shell
    pip install .
    patchright install chrome
    ```

1. Create a folder called `env` in the project directory with one or more files each containing the following contents:

    ```bash
    CONFIG_NAME=<NAME SHOWN WHEN REFERRING TO THIS CONFIG (OPTIONAL)>
    SEARCH_URL=<URL OF THE ZILLOW CLONE TO SCRAPE>
    FORM_URL=<YOUR GOOGLE FORMS URL (OPTIONAL)>
    SHEET_URL=<YOUR GOOGLE SHEETS DOC URL (OPTIONAL)>
    SHEET_NAME=<NAME OF SHEET IN GOOGLE SHEETS DOC TO ADD TO (OPTIONAL)>
    CSV_PATH=<PATH TO WRITE LISTINGS AS CSV (OPTIONAL)>
    ```

    For example, `env/brunswick` for Brunswick, Maine rentals:

    ```bash
    CONFIG_NAME=brunswick-04011
    SEARCH_URL=https://www.zillow.com/brunswick-me-04011/rentals/
    CSV_PATH=output/brunswick_04011.csv
    ```

1. (Optional) To rotate through proxies, copy `proxies.example.txt` to `proxies.txt` and list one proxy per line.
   Each proxy gets a fresh browser profile and scrapes `PAGES_PER_PROXY` result pages before the next proxy
   resumes at the following page. A proxy that hits a CAPTCHA or connection error is rested and a different
   proxy retries the same page. Without `proxies.txt` the scraper connects directly.

1. (Optional) Copy `.env.example` to `.env` to change pacing. By default the scraper waits a random 20–45 seconds
   between page loads (across all proxies combined, so adding proxies never speeds up requests), and backs off
   exponentially (2 min, 4 min, 8 min... up to 30 min) after a block.

1. If using batch submission to a Google Sheet, add your [Google Service Worker authentication file](https://docs.gspread.org/en/latest/oauth2.html#for-bots-using-service-account) in the project root as ".service_account.json".

1. Run the script:

    ```shell
    python -m src/main.py
    ```

The script will open a Chrome window, scrape data from each page of results, and submit each entry into your form, sheet, and/or CSV.

## How listings are read

Zillow's results pages embed their search results as JSON (`<script id="__NEXT_DATA__">`, under
`props.pageProps.searchPageState.cat1`). The scraper reads listings and the next-page URL from that JSON, which also
provides beds, baths, square footage, home type, days on Zillow, rent Zestimate, coordinates, and zpid. If the JSON
is missing it falls back to parsing the HTML listing cards.
