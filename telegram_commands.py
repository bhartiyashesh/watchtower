"""
Smart Lock System — Telegram Command Handler
==============================================
Polls for incoming Telegram messages via getUpdates and dispatches bot
commands (/status, /unlock, /lock, /snap, /events, /mute, /help).

Runs as a background asyncio.Task alongside the Ring polling loop.
Uses the same ExtBot instance pattern as TelegramAlerter — no
Application.run_polling, so it coexists with uvicorn's event loop.

Security: Only messages from the configured TELEGRAM_CHAT_ID are processed.
"""

import asyncio
import io
import logging
import time

from telegram.error import TelegramError

logger = logging.getLogger("smart-lock.commands")

POLL_INTERVAL: float = 2.0


class TelegramCommandHandler:
    """
    Background command handler that polls Telegram getUpdates.

    Args:
        alerter: TelegramAlerter instance (provides bot + mute control).
        switchbot: SwitchBotClient instance for lock operations.
        store: EventStore instance for event queries.
        ring: RingClient instance for live snapshots.
        chat_id: Authorized Telegram chat ID (string or int).
    """

    def __init__(self, alerter, switchbot, store, ring, chat_id: str | int) -> None:
        self._bot = alerter._bot
        self._alerter = alerter
        self._switchbot = switchbot
        self._store = store
        self._ring = ring
        self._chat_id = str(chat_id)
        self._offset: int = 0

    async def run(self) -> None:
        """Main polling loop — call via asyncio.create_task(handler.run())."""
        logger.info("Telegram command handler started (polling every %.1fs)", POLL_INTERVAL)
        while True:
            try:
                updates = await self._bot.get_updates(
                    offset=self._offset, timeout=10
                )
                for update in updates:
                    self._offset = update.update_id + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                logger.info("Telegram command handler stopping")
                raise
            except TelegramError as exc:
                logger.error("getUpdates failed: %s", exc)
            except Exception:
                logger.exception("Unexpected error in command handler")
            await asyncio.sleep(POLL_INTERVAL)

    async def _handle_update(self, update) -> None:
        """Route an incoming update to the appropriate command handler."""
        message = update.message
        if message is None:
            return

        # Security: ignore messages from unauthorized chats
        if str(message.chat_id) != self._chat_id:
            logger.warning(
                "Ignoring message from unauthorized chat_id=%s", message.chat_id
            )
            return

        text = (message.text or "").strip()
        if not text.startswith("/"):
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # strip @botname suffix
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/status": self._cmd_status,
            "/unlock": self._cmd_unlock,
            "/lock": self._cmd_lock,
            "/snap": self._cmd_snap,
            "/events": self._cmd_events,
            "/mute": self._cmd_mute,
            "/help": self._cmd_help,
            "/start": self._cmd_help,
        }

        handler = handlers.get(command)
        if handler is None:
            await self._reply(message.chat_id, f"Unknown command: {command}\nSend /help for available commands.")
            return

        try:
            await handler(message.chat_id, args)
        except Exception:
            logger.exception("Error handling command %s", command)
            await self._reply(message.chat_id, f"Error processing {command}. Check server logs.")

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    async def _cmd_status(self, chat_id: int, args: str) -> None:
        """Report current lock status."""
        await self._reply(chat_id, "Checking lock status...")
        status = await asyncio.to_thread(self._switchbot.get_lock_status)
        if status is None:
            await self._reply(chat_id, "Could not retrieve lock status.")
            return

        lock_state = status.get("lockState", "unknown")
        battery = status.get("battery", "?")
        emoji = {"locked": "🔒", "unlocked": "🔓"}.get(lock_state, "❓")
        await self._reply(
            chat_id,
            f"{emoji} Lock state: {lock_state}\n🔋 Battery: {battery}%",
        )

    async def _cmd_unlock(self, chat_id: int, args: str) -> None:
        """Unlock the door."""
        await self._reply(chat_id, "🔓 Sending unlock command...")
        success = await asyncio.to_thread(self._switchbot.unlock)
        if success:
            await self._reply(chat_id, "Door unlocked successfully.")
        else:
            await self._reply(chat_id, "Unlock failed — check server logs.")

    async def _cmd_lock(self, chat_id: int, args: str) -> None:
        """Lock the door."""
        await self._reply(chat_id, "🔒 Sending lock command...")
        success = await asyncio.to_thread(self._switchbot.lock)
        if success:
            await self._reply(chat_id, "Door locked successfully.")
        else:
            await self._reply(chat_id, "Lock failed — check server logs.")

    async def _cmd_snap(self, chat_id: int, args: str) -> None:
        """Capture a live snapshot from Ring and send as photo."""
        await self._reply(chat_id, "📸 Capturing snapshot...")
        try:
            snapshot_bytes = await self._ring.doorbell.async_get_snapshot()
            if snapshot_bytes:
                await self._bot.send_photo(
                    chat_id=chat_id,
                    photo=io.BytesIO(snapshot_bytes),
                    caption="Live snapshot from Ring doorbell",
                )
                return
        except Exception:
            logger.exception("Snapshot capture failed")
        await self._reply(chat_id, "Could not capture snapshot.")

    async def _cmd_events(self, chat_id: int, args: str) -> None:
        """Show last 5 events summary."""
        events = await self._store.get_recent_events(limit=5)
        if not events:
            await self._reply(chat_id, "No events recorded yet.")
            return

        lines = ["📋 Last 5 events:\n"]
        for e in events:
            ts = e.get("recorded_at", "?")[:19]
            etype = e.get("event_type", "?")
            person = e.get("person_name") or "stranger"
            action = e.get("door_action", "none")
            det_labels = ", ".join(d["label"] for d in e.get("detections", []))
            line = f"• {ts} | {etype} | {person} | {action}"
            if det_labels:
                line += f" | [{det_labels}]"
            lines.append(line)

        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_mute(self, chat_id: int, args: str) -> None:
        """Mute alerts for N minutes (default 30)."""
        args = args.strip()
        if args == "off":
            self._alerter.unmute()
            await self._reply(chat_id, "🔔 Alerts unmuted.")
            return

        try:
            minutes = int(args) if args else 30
        except ValueError:
            await self._reply(chat_id, "Usage: /mute <minutes> or /mute off")
            return

        if minutes < 1 or minutes > 1440:
            await self._reply(chat_id, "Please specify 1–1440 minutes.")
            return

        self._alerter.mute(minutes)
        await self._reply(chat_id, f"🔇 Alerts muted for {minutes} minutes.\nSend /mute off to unmute.")

    async def _cmd_help(self, chat_id: int, args: str) -> None:
        """List available commands."""
        text = (
            "🏠 Smart Lock Bot Commands:\n\n"
            "/status — Check lock state & battery\n"
            "/unlock — Unlock the door\n"
            "/lock — Lock the door\n"
            "/snap — Live Ring camera snapshot\n"
            "/events — Last 5 events summary\n"
            "/mute <min> — Mute alerts (default 30 min)\n"
            "/mute off — Unmute alerts\n"
            "/help — Show this message"
        )
        await self._reply(chat_id, text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _reply(self, chat_id: int, text: str) -> None:
        """Send a text reply, swallowing TelegramError."""
        try:
            await self._bot.send_message(chat_id=chat_id, text=text)
        except TelegramError as exc:
            logger.error("Failed to send reply: %s", exc)
