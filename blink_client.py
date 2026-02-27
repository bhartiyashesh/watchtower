import json
import logging
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


class BlinkClient:
    """Minimal Blink camera client for fetching snapshots with 2FA support."""

    def __init__(self, username: str, password: str, camera_name: str = ""):
        self.username = username
        self.password = password
        self.camera_name = camera_name
        self.blink: Blink | None = None
        self._camera = None
        self.needs_2fa: bool = False

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
            self.blink.auth = Auth(json_load(str(BLINK_CREDS_CACHE)), no_prompt=True)

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

        try:
            result = await self.blink.auth.send_auth_key(self.blink, code)
            if not result:
                logger.warning("Blink 2FA code rejected")
                return False
        except AttributeError:
            # Newer blinkpy versions use send_2fa_code instead
            try:
                result = await self.blink.send_2fa_code(code)
                if not result:
                    logger.warning("Blink 2FA code rejected")
                    return False
            except Exception:
                logger.exception("Blink 2FA submission failed")
                return False
        except Exception:
            logger.exception("Blink 2FA submission failed")
            return False

        self.needs_2fa = False
        self._save_creds()

        # Now set up cameras
        await self.blink.setup_post_verify()
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
            image_bytes = await self._camera.get_media()
            self._save_creds()
            return image_bytes if image_bytes else None
        except Exception:
            logger.exception("Failed to get Blink snapshot")
            return None

    def _save_creds(self) -> None:
        """Persist auth credentials to cache file."""
        if self.blink and self.blink.auth:
            BLINK_CREDS_CACHE.write_text(json.dumps(self.blink.auth.login_attributes))

    async def stop(self) -> None:
        """Cleanup resources."""
        self.blink = None
        self._camera = None
