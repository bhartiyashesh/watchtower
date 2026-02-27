import asyncio
import json
import logging
import tempfile
import os
from pathlib import Path
from ring_doorbell import Auth, Ring
from ring_doorbell.exceptions import Requires2FAError

logger = logging.getLogger(__name__)

TOKEN_CACHE = Path(".ring_token.cache")
FCM_CREDENTIALS_CACHE = Path(".fcm_credentials.cache")


class RingClient:
    """Handles Ring Doorbell authentication, event detection, and image capture."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.ring: Ring | None = None
        self.doorbell = None
        self._last_event_id: str | None = None
        self._listener = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._use_push = False

    def _token_updated(self, token: dict) -> None:
        TOKEN_CACHE.write_text(json.dumps(token))

    def _fcm_credentials_updated(self, creds: dict) -> None:
        FCM_CREDENTIALS_CACHE.write_text(json.dumps(creds))

    async def authenticate(self) -> None:
        """Authenticate with Ring. Uses cached token if available."""
        if TOKEN_CACHE.exists():
            token_data = json.loads(TOKEN_CACHE.read_text())
            auth = Auth("SmartLockSystem/1.0", token_data, self._token_updated)
        else:
            auth = Auth("SmartLockSystem/1.0", None, self._token_updated)
            try:
                await auth.async_fetch_token(self.username, self.password)
            except Requires2FAError:
                code = input("Ring 2FA code (check your email/SMS): ")
                await auth.async_fetch_token(self.username, self.password, otp_code=code)

        self.ring = Ring(auth)
        await self.ring.async_update_data()

        doorbells = self.ring.devices()["doorbots"]
        if not doorbells:
            raise RuntimeError("No Ring doorbell found on this account")

        self.doorbell = doorbells[0]
        logger.info(f"Connected to Ring doorbell: {self.doorbell.name}")

    async def try_start_listener(self) -> bool:
        """Try to start push notification listener. Returns True if successful."""
        try:
            from ring_doorbell.listen import RingEventListener

            fcm_creds = None
            if FCM_CREDENTIALS_CACHE.exists():
                fcm_creds = json.loads(FCM_CREDENTIALS_CACHE.read_text())

            self._listener = RingEventListener(
                self.ring,
                credentials=fcm_creds,
                credentials_updated_callback=self._fcm_credentials_updated,
            )

            def on_event(event):
                if event.kind in ("motion", "ding") and not event.is_update:
                    logger.info(f"[PUSH] Event: id={event.id} kind={event.kind}")
                    self._event_queue.put_nowait(event)

            self._listener.add_notification_callback(on_event)
            started = await self._listener.start()
            if started:
                self._use_push = True
                logger.info("Push notification listener active")
            return started
        except Exception as e:
            logger.warning(f"Push listener failed: {e}")
            return False

    async def wait_for_event(self, poll_interval: float = 2) -> tuple[int, str] | None:
        """Poll for new events. Returns (recording_id, kind) tuple or None."""
        # Only call history — skip async_update_data() which makes 3 extra API calls
        history = await self.doorbell.async_history(limit=1)
        if not history:
            return None

        latest = history[0]
        event_id = str(latest["id"])

        if self._last_event_id is None:
            self._last_event_id = event_id
            logger.info(f"Baseline event: {event_id} (kind={latest.get('kind')})")
            return None

        if event_id != self._last_event_id:
            self._last_event_id = event_id
            logger.info(f"New event: {event_id} (kind={latest.get('kind')})")
            kind = latest.get("kind", "motion")
            return latest["id"], kind

        return None

    async def capture_frame(self, recording_id: int) -> bytes | None:
        """Download the event recording and extract a frame.
        Retries since Ring needs time to process recordings."""
        for attempt in range(1, 25):
            try:
                video_bytes = await self.doorbell.async_recording_download(recording_id)
                if video_bytes:
                    logger.info(f"Recording downloaded: {len(video_bytes)} bytes "
                                f"(attempt {attempt})")
                    return self._extract_frame(video_bytes)
            except Exception:
                pass
            if attempt == 1:
                logger.info("Waiting for Ring to process recording...")
            await asyncio.sleep(5)

        logger.error("All recording download attempts failed")
        return None

    def get_battery_level(self) -> int | None:
        """Return the doorbell's battery percentage, or None if unavailable."""
        try:
            return self.doorbell.battery_life
        except Exception:
            return None

    async def stop(self) -> None:
        """Stop the event listener if running."""
        if self._listener:
            await self._listener.stop()

    @staticmethod
    async def test_credentials(username: str, password: str) -> str:
        """Test Ring credentials. Returns '2fa_required' or 'success'.

        Raises an exception if the credentials are wrong.
        """
        auth = Auth("WatchTower/1.0", None, lambda t: None)
        try:
            await auth.async_fetch_token(username, password)
            return "success"
        except Requires2FAError:
            return "2fa_required"

    @staticmethod
    async def complete_2fa(username: str, password: str, otp_code: str) -> None:
        """Complete 2FA verification. Writes token cache on success.

        Ring's async_fetch_token is stateless — a fresh Auth instance
        with the OTP code works without preserving the original Auth object.
        """
        auth = Auth(
            "WatchTower/1.0",
            None,
            lambda t: TOKEN_CACHE.write_text(json.dumps(t)),
        )
        await auth.async_fetch_token(username, password, otp_code=otp_code)

    @staticmethod
    def _extract_frame(video_bytes: bytes) -> bytes | None:
        """Extract a frame from MP4 video bytes using OpenCV."""
        import cv2

        tmp_path = tempfile.mktemp(suffix=".mp4")
        try:
            with open(tmp_path, "wb") as f:
                f.write(video_bytes)
            cap = cv2.VideoCapture(tmp_path)
            # Seek to 1 second in (skip initial blurry frames)
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return None
            _, jpg = cv2.imencode(".jpg", frame)
            return jpg.tobytes()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
