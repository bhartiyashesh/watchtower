"""
Comprehensive test suite for Phase 4 Telegram Alerts.

Validates all TELE requirements (TELE-01 through TELE-05):
  TELE-01: Stranger alert sends photo + caption when unrecognized person detected
  TELE-02: Unlock alert sends photo + caption when known person unlocks door
  TELE-03: Photo and caption sent as single message (not separate)
  TELE-04: 60-second per-type coalescing suppresses duplicate alerts
  TELE-05: AIORateLimiter + RetryAfter handling prevents Telegram flood ban

Mocking strategy:
  - ExtBot: replaced with AsyncMock on alerter._bot — no real Telegram API calls
  - All async bot methods (send_photo, send_message, initialize, get_chat) are AsyncMock
  - asyncio.sleep patched where needed to avoid real waits in retry tests
"""

import asyncio
import io
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from PIL import Image

from telegram.error import RetryAfter, TelegramError
from telegram.ext import AIORateLimiter, ExtBot

from telegram_alerter import TelegramAlerter, COALESCE_SECONDS


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_jpeg_image(width: int = 100, height: int = 100, color=(255, 0, 0)) -> bytes:
    """Create a synthetic JPEG image in memory."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """
    AsyncMock representing an ExtBot instance.
    All relevant async methods are set as AsyncMock so they can be awaited
    and inspected for call count / call args.
    """
    bot = MagicMock(spec=ExtBot)
    bot.send_photo = AsyncMock(return_value=None)
    bot.send_message = AsyncMock(return_value=None)
    bot.initialize = AsyncMock(return_value=None)
    bot.shutdown = AsyncMock(return_value=None)
    bot.get_chat = AsyncMock(return_value=MagicMock(title="Test Chat", username=None))
    return bot


@pytest_asyncio.fixture
async def alerter(mock_bot):
    """
    TelegramAlerter with _bot replaced by mock_bot and _chat_id set to a
    test value. Each test gets a fresh alerter (function-scoped fixture)
    so coalescing state does not leak between tests.
    """
    a = TelegramAlerter(token="test_token_placeholder", chat_id="test_chat_123")
    # Replace the real ExtBot with our mock — avoids any HTTP connection
    a._bot = mock_bot
    a._chat_id = "test_chat_123"
    return a


@pytest.fixture
def tmp_thumbnail(tmp_path):
    """
    Create a temporary JPEG file (100x100 red image) in a temp directory.
    Yields the file path as a string. File is cleaned up automatically by
    tmp_path fixture teardown.
    """
    thumb_path = tmp_path / "test_thumbnail.jpg"
    thumb_path.write_bytes(make_jpeg_image())
    yield str(thumb_path)


# ---------------------------------------------------------------------------
# TELE-01 + TELE-03: Stranger alert sends photo with caption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_stranger_sends_photo_with_caption(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-01, TELE-03: alert_stranger() sends a single send_photo call with
    the correct chat_id, caption, and a non-None photo file handle.
    send_message must NOT be called (single combined message).
    """
    await alerter.alert_stranger(tmp_thumbnail, "Unknown person at door")

    mock_bot.send_photo.assert_called_once()
    call_kwargs = mock_bot.send_photo.call_args.kwargs
    assert call_kwargs["chat_id"] == "test_chat_123"
    assert call_kwargs["caption"] == "Unknown person at door"
    assert call_kwargs["photo"] is not None  # File handle, not None

    mock_bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# TELE-02 + TELE-03: Unlock alert sends photo with caption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_unlock_sends_photo_with_caption(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-02, TELE-03: alert_unlock() sends a single send_photo call with
    the correct chat_id and caption. send_message must NOT be called.
    """
    await alerter.alert_unlock(tmp_thumbnail, "Welcome home, Alice!")

    mock_bot.send_photo.assert_called_once()
    call_kwargs = mock_bot.send_photo.call_args.kwargs
    assert call_kwargs["chat_id"] == "test_chat_123"
    assert call_kwargs["caption"] == "Welcome home, Alice!"
    assert call_kwargs["photo"] is not None

    mock_bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# TELE-04: 60-second coalescing — suppression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stranger_coalescing_suppresses_within_60s(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-04: Two stranger alerts sent back-to-back (within 60s) — only the
    first should result in a send_photo call. The second is suppressed.
    """
    await alerter.alert_stranger(tmp_thumbnail, "First alert")
    await alerter.alert_stranger(tmp_thumbnail, "Second alert")

    assert mock_bot.send_photo.call_count == 1, (
        f"Expected 1 send_photo call (second suppressed), got {mock_bot.send_photo.call_count}"
    )


@pytest.mark.asyncio
async def test_stranger_coalescing_allows_after_60s(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-04: After 61 seconds have elapsed since the last stranger alert,
    a new stranger alert is allowed through (not suppressed).
    """
    await alerter.alert_stranger(tmp_thumbnail, "First alert")

    # Simulate 61 seconds elapsed by back-dating the last alert timestamp
    alerter._last_stranger_alert = time.monotonic() - 61

    await alerter.alert_stranger(tmp_thumbnail, "Second alert")

    assert mock_bot.send_photo.call_count == 2, (
        f"Expected 2 send_photo calls (both allowed), got {mock_bot.send_photo.call_count}"
    )


@pytest.mark.asyncio
async def test_unlock_coalescing_suppresses_within_60s(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-04: Two unlock alerts sent back-to-back (within 60s) — only the
    first should result in a send_photo call. The second is suppressed.
    """
    await alerter.alert_unlock(tmp_thumbnail, "First unlock")
    await alerter.alert_unlock(tmp_thumbnail, "Second unlock")

    assert mock_bot.send_photo.call_count == 1, (
        f"Expected 1 send_photo call (second suppressed), got {mock_bot.send_photo.call_count}"
    )


@pytest.mark.asyncio
async def test_stranger_and_unlock_coalescing_independent(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-04: Stranger and unlock coalescing use INDEPENDENT timestamps.
    A stranger alert immediately followed by an unlock alert should both be
    sent — they must never suppress each other.
    """
    await alerter.alert_stranger(tmp_thumbnail, "Stranger detected")
    await alerter.alert_unlock(tmp_thumbnail, "Door unlocked for Alice")

    assert mock_bot.send_photo.call_count == 2, (
        f"Expected 2 send_photo calls (stranger and unlock are independent), "
        f"got {mock_bot.send_photo.call_count}"
    )


# ---------------------------------------------------------------------------
# TELE-05: AIORateLimiter configured on ExtBot
# ---------------------------------------------------------------------------


def test_rate_limiter_configured():
    """
    TELE-05: TelegramAlerter constructor passes an AIORateLimiter to ExtBot.
    Verified by inspecting _bot._rate_limiter — must not be None.
    """
    a = TelegramAlerter(token="test_token_12345", chat_id="test_chat_999")
    assert a._bot._rate_limiter is not None, (
        "Expected ExtBot to be initialized with an AIORateLimiter, got None"
    )


# ---------------------------------------------------------------------------
# TELE-05: RetryAfter exception triggers one retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_after_exception_retries_once(alerter, mock_bot, tmp_thumbnail):
    """
    TELE-05: When send_photo raises RetryAfter on the first attempt, the
    alerter waits (asyncio.sleep) and retries exactly once. Total send_photo
    call count must be 2 (initial + 1 retry).
    """
    # First call raises RetryAfter(1), second call succeeds
    mock_bot.send_photo.side_effect = [RetryAfter(1), None]

    with patch("telegram_alerter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await alerter.alert_stranger(tmp_thumbnail, "RetryAfter test")

    assert mock_bot.send_photo.call_count == 2, (
        f"Expected 2 send_photo calls (1 initial + 1 retry), "
        f"got {mock_bot.send_photo.call_count}"
    )
    # asyncio.sleep should have been called with the retry_after duration (~1s)
    mock_sleep.assert_called_once()
    sleep_arg = mock_sleep.call_args.args[0]
    assert abs(sleep_arg - 1.0) < 0.1, (
        f"Expected sleep ~1.0s, got {sleep_arg}"
    )


# ---------------------------------------------------------------------------
# Robustness: TelegramError does not crash the pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_error_does_not_crash(alerter, mock_bot, tmp_thumbnail):
    """
    Robustness: If send_photo raises TelegramError, the alerter catches it
    and logs without re-raising. The pipeline must never crash due to Telegram
    being unavailable.
    """
    mock_bot.send_photo.side_effect = TelegramError("API unavailable")

    # This must NOT raise — TelegramError is handled internally
    await alerter.alert_stranger(tmp_thumbnail, "Error handling test")

    # Verify the exception was swallowed (no assertion on return value)
    assert mock_bot.send_photo.called


# ---------------------------------------------------------------------------
# TELE-03 edge cases: thumbnail fallback to text-only message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_thumbnail_falls_back_to_text(alerter, mock_bot):
    """
    TELE-03 edge case: If thumbnail_path points to a non-existent file,
    send_message (text-only) is used instead of send_photo.
    """
    await alerter.alert_stranger("/nonexistent/path/thumbnail.jpg", "Stranger detected")

    mock_bot.send_message.assert_called_once()
    mock_bot.send_photo.assert_not_called()


@pytest.mark.asyncio
async def test_none_thumbnail_falls_back_to_text(alerter, mock_bot):
    """
    TELE-03 edge case: If thumbnail_path is None, send_message (text-only)
    is used instead of send_photo. None indicates no thumbnail was captured.
    """
    await alerter.alert_stranger(None, "Stranger detected")

    mock_bot.send_message.assert_called_once()
    mock_bot.send_photo.assert_not_called()


# ---------------------------------------------------------------------------
# Startup: initialize validates bot and chat reachability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_validates_chat_reachability(alerter, mock_bot):
    """
    Startup: alerter.initialize() calls bot.initialize() and bot.get_chat()
    with the correct chat_id to validate reachability before the polling loop starts.
    """
    await alerter.initialize()

    mock_bot.initialize.assert_called_once()
    mock_bot.get_chat.assert_called_once_with(chat_id="test_chat_123")
