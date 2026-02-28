"""
Smart Lock System — Telegram Alerter
=====================================
Provides TelegramAlerter, an async helper that sends photo alerts to a
Telegram chat when an unrecognized person is detected or a known resident
unlocks the door.

Design decisions (from Phase 4 research):
- Uses ExtBot + AIORateLimiter (not Application.run_polling) to avoid
  creating a second asyncio event loop that conflicts with uvicorn.
- 60-second coalescing per alert type prevents Telegram flood bans from
  burst Ring motion events.
- Lock is released before HTTP I/O so coalescing never blocks other
  coroutines while a slow send is in progress (research pitfall #2).
- TelegramError is caught and logged — Telegram unavailability must never
  prevent door unlock or event persistence.
"""

import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path

from telegram.error import RetryAfter, TelegramError
from telegram.ext import AIORateLimiter, ExtBot

logger = logging.getLogger("smart-lock.telegram")

# Duplicate alerts within this window are silently suppressed (TELE-04)
COALESCE_SECONDS: int = 60


class TelegramAlerter:
    """
    Async Telegram alerter with per-type coalescing and proactive rate limiting.

    Usage:
        alerter = TelegramAlerter(token="...", chat_id="...")
        await alerter.initialize()
        await alerter.alert_stranger(thumbnail_path, caption)
        await alerter.alert_unlock(thumbnail_path, caption)
        await alerter.shutdown()
    """

    def __init__(self, token: str, chat_id: str | int) -> None:
        """
        Initialize the alerter with a bot token and target chat ID.

        Args:
            token: Telegram Bot API token from @BotFather.
            chat_id: Telegram chat ID to send alerts to.
        """
        self._bot = ExtBot(
            token=token,
            rate_limiter=AIORateLimiter(max_retries=1),
        )
        self._chat_id = chat_id

        # Separate monotonic timestamps per alert type so stranger and unlock
        # alerts never suppress each other (TELE-04 requirement)
        self._last_stranger_alert: float = 0.0
        self._last_unlock_alert: float = 0.0
        self._last_blink_motion_alert: float = 0.0

        # Mute support — monotonic timestamp until which alerts are suppressed
        self._muted_until: float = 0.0

        # Single asyncio.Lock guards timestamp reads and updates only
        # (released BEFORE any HTTP I/O — see _send_photo call sites)
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """
        Open the HTTPX connection pool and validate chat reachability.

        On TelegramError (bad token, bot blocked, network down): logs the
        error but does NOT raise — Telegram unavailability must not prevent
        door unlock or event logging from functioning.
        """
        await self._bot.initialize()
        try:
            chat = await self._bot.get_chat(chat_id=self._chat_id)
            logger.info(
                f"Telegram alerter ready — chat_id={self._chat_id}, "
                f"chat_title={getattr(chat, 'title', None) or getattr(chat, 'username', None)}"
            )
        except TelegramError as exc:
            logger.error(
                f"Telegram chat validation failed (alerts disabled): {exc}"
            )

    @property
    def is_muted(self) -> bool:
        """True if alerts are currently muted."""
        return time.monotonic() < self._muted_until

    def mute(self, minutes: int = 30) -> None:
        """Suppress all alerts for the given number of minutes."""
        self._muted_until = time.monotonic() + minutes * 60
        logger.info("Alerts muted for %d minutes", minutes)

    def unmute(self) -> None:
        """Cancel any active mute immediately."""
        self._muted_until = 0.0
        logger.info("Alerts unmuted")

    async def shutdown(self) -> None:
        """Close the HTTPX connection pool cleanly."""
        await self._bot.shutdown()

    async def alert_stranger(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """
        Send a stranger-detected alert with photo and caption.

        Suppressed silently if called within COALESCE_SECONDS of the
        previous stranger alert, or if alerts are muted.

        Args:
            thumbnail_path: Absolute path to a JPEG thumbnail, or None.
            caption: Human-readable alert caption.
        """
        if self.is_muted:
            logger.debug("Stranger alert suppressed (muted)")
            return
        async with self._lock:
            now = time.monotonic()
            if now - self._last_stranger_alert < COALESCE_SECONDS:
                logger.debug(
                    "Stranger alert suppressed (within coalesce window)"
                )
                return
            self._last_stranger_alert = now
        # Lock is released BEFORE the HTTP call — never hold lock across I/O
        await self._send_photo(thumbnail_path, caption)

    async def alert_unlock(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """
        Send an unlock-confirmed alert with photo and caption.

        Suppressed silently if called within COALESCE_SECONDS of the
        previous unlock alert, or if alerts are muted.

        Args:
            thumbnail_path: Absolute path to a JPEG thumbnail, or None.
            caption: Human-readable alert caption.
        """
        if self.is_muted:
            logger.debug("Unlock alert suppressed (muted)")
            return
        async with self._lock:
            now = time.monotonic()
            if now - self._last_unlock_alert < COALESCE_SECONDS:
                logger.debug(
                    "Unlock alert suppressed (within coalesce window)"
                )
                return
            self._last_unlock_alert = now
        # Lock is released BEFORE the HTTP call — never hold lock across I/O
        await self._send_photo(thumbnail_path, caption)

    async def alert_blink_motion(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """
        Send a Blink motion alert for non-person detections (cars, animals, packages).

        Uses its own coalescing timestamp so it doesn't interfere with Ring's
        stranger or unlock alert types.

        Args:
            thumbnail_path: Absolute path to a JPEG thumbnail, or None.
            caption: Human-readable alert caption.
        """
        if self.is_muted:
            logger.debug("Blink motion alert suppressed (muted)")
            return
        async with self._lock:
            now = time.monotonic()
            if now - self._last_blink_motion_alert < COALESCE_SECONDS:
                logger.debug(
                    "Blink motion alert suppressed (within coalesce window)"
                )
                return
            self._last_blink_motion_alert = now
        # Lock is released BEFORE the HTTP call — never hold lock across I/O
        await self._send_photo(thumbnail_path, caption)

    async def _send_photo(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """
        Send a single Telegram message (photo + caption, or text fallback).

        Implements first-try + one-retry on RetryAfter. All TelegramError
        exceptions are caught and logged — never propagated.

        Args:
            thumbnail_path: Path to thumbnail file, or None for text-only.
            caption: Alert text (used as photo caption or plain message).
        """
        for attempt in range(2):  # first try + one RetryAfter retry
            try:
                if thumbnail_path is not None and Path(thumbnail_path).exists():
                    with open(thumbnail_path, "rb") as photo:
                        await self._bot.send_photo(
                            chat_id=self._chat_id,
                            photo=photo,
                            caption=caption,
                        )
                else:
                    # Thumbnail missing or unavailable — fall back to text
                    if thumbnail_path is not None:
                        logger.warning(
                            f"Thumbnail not found at {thumbnail_path!r}, "
                            "sending text alert instead"
                        )
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=caption,
                    )
                return  # Success — exit loop

            except RetryAfter as exc:
                # PTB v22 may return retry_after as int or timedelta
                wait = (
                    exc.retry_after.total_seconds()
                    if isinstance(exc.retry_after, timedelta)
                    else float(exc.retry_after)
                )
                logger.warning(
                    f"Telegram RetryAfter: waiting {wait:.1f}s "
                    f"(attempt {attempt + 1}/2)"
                )
                await asyncio.sleep(wait)
                # continue to next iteration for retry

            except TelegramError as exc:
                logger.error(f"Telegram send failed: {exc}")
                return  # Do not crash the pipeline
