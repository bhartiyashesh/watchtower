import asyncio
import json
import logging
import ssl
from pathlib import Path

from blinkpy.blinkpy import Blink
from blinkpy.auth import Auth
from blinkpy.helpers.util import json_load

from _paths import RUNTIME_DIR

logger = logging.getLogger(__name__)

BLINK_CREDS_CACHE = RUNTIME_DIR / ".blink_creds.cache"

# Import 2FA exception — name varies by blinkpy version
try:
    from blinkpy.auth import BlinkTwoFARequiredError
except ImportError:
    # Older versions don't have this; fall back to a sentinel
    BlinkTwoFARequiredError = None


async def _patched_recv(self):
    """Fixed recv() that uses readexactly() instead of read().

    blinkpy's BlinkLiveStream.recv() uses asyncio read(n) for both the
    9-byte IMMIS header and the payload.  read(n) may return *fewer* than
    n bytes on a slow or bursty network — the upstream code treats that as
    a fatal error ("Insufficient data for payload") and kills the stream.

    readexactly(n) blocks until all n bytes arrive (or raises
    IncompleteReadError on true EOF), which is the correct behaviour for
    a framed binary protocol.
    """
    try:
        logger.debug("patched recv: starting")
        while not self.target_reader.at_eof():
            header = await self.target_reader.readexactly(9)

            msgtype = header[0]
            payload_length = int.from_bytes(header[5:9], byteorder="big")

            if payload_length <= 0:
                continue

            data = await self.target_reader.readexactly(payload_length)

            # Only forward video packets (msgtype 0x00 starting with TS sync byte)
            if msgtype != 0x00 or data[0] != 0x47:
                continue

            for writer in self.clients:
                if not writer.is_closing():
                    writer.write(data)
                    await writer.drain()

            await asyncio.sleep(0)
    except asyncio.IncompleteReadError:
        logger.debug("patched recv: stream ended (incomplete read)")
    except ssl.SSLError as e:
        if e.reason != "APPLICATION_DATA_AFTER_CLOSE_NOTIFY":
            logger.exception("patched recv: SSL error")
    except Exception:
        logger.exception("patched recv: error")
    finally:
        if self.target_writer and not self.target_writer.is_closing():
            self.target_writer.close()
        logger.debug("patched recv: done")


class BlinkClient:
    """Minimal Blink camera client for fetching snapshots with 2FA support."""

    def __init__(self, username: str, password: str, camera_name: str = ""):
        self.username = username
        self.password = password
        self.camera_name = camera_name
        self.blink: Blink | None = None
        self._camera = None
        self.needs_2fa: bool = False
        self._livestream = None
        self._feed_task: asyncio.Task | None = None

    async def authenticate(self) -> None:
        """Authenticate with Blink servers, caching credentials to disk.

        If 2FA is required, sets self.needs_2fa = True instead of raising.
        Call submit_2fa(code) to complete authentication.
        """
        self.blink = Blink()
        self.blink.auth = Auth(
            {"username": self.username, "password": self.password},
            no_prompt=True,
        )

        if BLINK_CREDS_CACHE.exists():
            cached = json_load(str(BLINK_CREDS_CACHE))
            # blinkpy >=0.22 made json_load async
            if hasattr(cached, "__await__"):
                cached = await cached
            self.blink.auth = Auth(cached, no_prompt=True)

        exceptions_to_catch = (BlinkTwoFARequiredError,) if BlinkTwoFARequiredError else ()

        try:
            await self.blink.start()
        except exceptions_to_catch:
            self.needs_2fa = True
            logger.info("Blink 2FA required — waiting for verification code")
            return

        self.needs_2fa = False
        self._save_creds()
        self._camera = self._find_camera()
        if self._camera:
            logger.info(f"Connected to Blink camera: {self._camera.name}")
        else:
            logger.warning("No Blink cameras found on this account")

    async def submit_2fa(self, code: str) -> bool:
        """Submit a 2FA verification code to complete authentication.

        Returns True on success, False on failure.
        """
        if not self.blink:
            return False

        # Blink.send_2fa_code is the correct high-level method: it calls
        # Auth.complete_2fa_login, then setup_urls, get_homescreen, and
        # setup_post_verify — so cameras are populated when it returns.
        try:
            result = await self.blink.send_2fa_code(code)
        except Exception:
            logger.exception("Blink 2FA submission failed")
            return False

        if not result:
            logger.warning("Blink 2FA code rejected")
            return False

        self.needs_2fa = False
        self._save_creds()
        self._camera = self._find_camera()
        if self._camera:
            logger.info(f"Connected to Blink camera: {self._camera.name}")
        else:
            logger.warning("No Blink cameras found after 2FA")

        return True

    def _find_camera(self):
        """Locate the camera by name, or return the first one available."""
        if not self.blink:
            return None

        for name, cam in self.blink.cameras.items():
            if self.camera_name and name.lower() == self.camera_name.lower():
                return cam

        # Fallback: return first camera
        if self.blink.cameras:
            first = next(iter(self.blink.cameras.values()))
            if self.camera_name:
                logger.warning(
                    f"Camera '{self.camera_name}' not found, using '{first.name}'"
                )
            return first

        return None

    async def get_snapshot(self) -> bytes | None:
        """Request a new snapshot and return JPEG bytes."""
        if self.needs_2fa or not self._camera:
            return None

        try:
            await self._camera.snap_picture()
            await self.blink.refresh(force=True)
            response = await self._camera.get_media()
            self._save_creds()
            if response is None:
                return None
            # Newer blinkpy returns an aiohttp ClientResponse; read the body
            if hasattr(response, "read"):
                return await response.read()
            return response if isinstance(response, bytes) else None
        except Exception:
            logger.exception("Failed to get Blink snapshot")
            return None

    def _save_creds(self) -> None:
        """Persist auth credentials to cache file."""
        if self.blink and self.blink.auth:
            BLINK_CREDS_CACHE.write_text(json.dumps(self.blink.auth.login_attributes))

    async def start_livestream(self) -> str:
        """Start a live stream and return the local TCP URL for FFmpeg.

        Returns the TCP URL (e.g. tcp://127.0.0.1:54321) where MPEG-TS
        packets are served by BlinkLiveStream.

        The IMMIS auth handshake is completed before returning, so the
        stream is ready to deliver data as soon as FFmpeg connects.
        Retries up to 3 times on transient Blink API failures.
        """
        if self.needs_2fa or not self._camera:
            raise RuntimeError("Camera not ready")

        # If already streaming, return the existing URL
        if self._livestream and self._livestream.is_serving:
            return self._livestream.url

        # Clean up any stale state
        await self.stop_livestream()

        # init_livestream() sometimes fails with KeyError when the Blink
        # API returns an incomplete response.  Retry a few times.
        stream = None
        for attempt in range(3):
            try:
                stream = await self._camera.init_livestream()
                break
            except (KeyError, NotImplementedError) as exc:
                logger.warning("init_livestream attempt %d failed: %s", attempt + 1, exc)
                if attempt == 2:
                    raise
                await asyncio.sleep(1)

        await stream.start()
        self._livestream = stream

        # Complete the IMMIS auth *before* returning so the connection
        # is ready to send data when FFmpeg connects to the TCP socket.
        await stream.auth()
        logger.info("Blink IMMIS auth complete, starting data flow")

        # Monkeypatch recv() to use readexactly() — blinkpy's recv() uses
        # read(n) which may return fewer bytes than requested on a slow
        # network, then treats the short read as fatal and kills the stream.
        stream.recv = _patched_recv.__get__(stream)

        # Start recv/send/poll as a background task (skip feed() which
        # would call auth() a second time).
        async def _data_flow():
            try:
                await asyncio.gather(stream.recv(), stream.send(), stream.poll())
            except Exception:
                logger.exception("Livestream data flow error")
            finally:
                stream.stop()

        self._feed_task = asyncio.create_task(_data_flow())
        logger.info("Blink livestream started at %s", stream.url)
        return stream.url

    async def stop_livestream(self) -> None:
        """Stop the live stream and clean up resources."""
        if self._feed_task and not self._feed_task.done():
            self._feed_task.cancel()
            try:
                await self._feed_task
            except (asyncio.CancelledError, Exception):
                pass
        self._feed_task = None

        if self._livestream:
            self._livestream.stop()
            self._livestream = None
            logger.info("Blink livestream stopped")

    async def stop(self) -> None:
        """Cleanup resources."""
        await self.stop_livestream()
        self.blink = None
        self._camera = None
