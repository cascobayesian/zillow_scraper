"""Tests for ZillowSearchJson (embedded __NEXT_DATA__ parsing)."""

import json

import pytest

from src.constants import ZillowParseError
from src.scraper import ZillowSearchJson


def _next_data(results: list[dict], next_url: str | None = "/somewhere-me/rentals/2_p/") -> str:
    return json.dumps(
        {
            "props": {
                "pageProps": {
                    "searchPageState": {
                        "cat1": {
                            "searchResults": {"listResults": results},
                            "searchList": {"totalResultCount": 99, "pagination": {"nextUrl": next_url} if next_url else {}},
                        }
                    }
                }
            }
        }
    )


SINGLE = {
    "address": "5 Midway Cir #2, Brunswick, ME 04011",
    "detailUrl": "https://www.zillow.com/apartments/brunswick-me/haven/abc/",
    "price": "$1,898/mo",
    "unformattedPrice": 1898,
    "beds": 2,
    "baths": 1,
    "area": 904,
    "latLong": {"latitude": 43.9, "longitude": -69.9},
    "hdpData": {"homeInfo": {"zpid": 123, "homeType": "APARTMENT", "daysOnZillow": 32, "rentZestimate": 2033}},
}

BUILDING = {
    "address": "5 Captains Way, Brunswick, ME",
    "detailUrl": "/apartments/brunswick-me/landing/xyz/",
    "zpid": "43.9--69.9",
    "latLong": {"latitude": 43.9, "longitude": -69.9},
    "units": [{"price": "$1,895+", "beds": "1"}, {"price": "Call", "beds": "0"}],
}


def test_single_listing_fields() -> None:
    parsed = ZillowSearchJson(_next_data([SINGLE]))
    [listing] = parsed.listings
    assert listing.address == SINGLE["address"]
    assert listing.price == "$1,898"
    assert listing.median_price == "1898"
    assert listing.link == SINGLE["detailUrl"]
    assert (listing.beds, listing.baths, listing.sqft) == ("2", "1", "904")
    assert (listing.home_type, listing.days_on_zillow, listing.rent_zestimate, listing.zpid) == ("APARTMENT", "32", "2033", "123")
    assert parsed.total_result_count == 99


def test_building_expands_to_floor_plans() -> None:
    listings = ZillowSearchJson(_next_data([BUILDING])).listings
    assert [listing.address for listing in listings] == ["5 Captains Way, Brunswick, ME (1 bd)", "5 Captains Way, Brunswick, ME (Studio)"]
    assert [listing.price for listing in listings] == ["$1,895", "Call"]
    assert [listing.median_price for listing in listings] == ["1895", "Call"]
    assert listings[0].link == "https://www.zillow.com/apartments/brunswick-me/landing/xyz/#bedrooms-1"


def test_next_url() -> None:
    assert ZillowSearchJson(_next_data([SINGLE])).next_url == "https://www.zillow.com/somewhere-me/rentals/2_p/"
    assert ZillowSearchJson(_next_data([SINGLE], next_url=None)).next_url is None


@pytest.mark.parametrize("text", ["not json", "{}", json.dumps({"props": {"pageProps": {}}})])
def test_unexpected_format_raises(text: str) -> None:
    with pytest.raises(ZillowParseError):
        ZillowSearchJson(text)
