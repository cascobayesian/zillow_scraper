"""Tests for throttle.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.throttle import RequestThrottle


@pytest.mark.asyncio
async def test_first_request_does_not_wait() -> None:
    throttle = RequestThrottle(min_delay=10, max_delay=20, backoff_base=60, backoff_max=600)
    with patch("src.throttle.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await throttle.wait()
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_request_waits_at_least_min_delay() -> None:
    throttle = RequestThrottle(min_delay=10, max_delay=20, backoff_base=60, backoff_max=600)
    with patch("src.throttle.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await throttle.wait()
        await throttle.wait()
    mock_sleep.assert_awaited_once()
    assert mock_sleep.await_args is not None
    delay = mock_sleep.await_args.args[0]
    assert 9 < delay <= 20


@pytest.mark.asyncio
async def test_backoff_grows_and_caps() -> None:
    throttle = RequestThrottle(min_delay=0, max_delay=0, backoff_base=60, backoff_max=200)
    with patch("src.throttle.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await throttle.backoff(1)
        await throttle.backoff(2)
        await throttle.backoff(5)
    delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert 48 <= delays[0] <= 72
    assert 96 <= delays[1] <= 144
    assert delays[2] <= 240


def test_invalid_range() -> None:
    with pytest.raises(ValueError, match="delay range"):
        RequestThrottle(min_delay=10, max_delay=5, backoff_base=1, backoff_max=1)
