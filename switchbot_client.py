import hashlib
import hmac
import base64
import logging
import time
import uuid

import requests

logger = logging.getLogger(__name__)

SWITCHBOT_API_BASE = "https://api.switch-bot.com/v1.1"


class SwitchBotClient:
    """Controls SwitchBot Lock via the Cloud API (routed through Hub Mini)."""

    def __init__(self, token: str, secret: str, device_id: str):
        self.token = token
        self.secret = secret
        self.device_id = device_id

    def _build_headers(self) -> dict:
        """Generate authenticated headers with HMAC-SHA256 signature (API v1.1)."""
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))

        sign_payload = f"{self.token}{timestamp}{nonce}"
        signature = base64.b64encode(
            hmac.new(
                self.secret.encode("utf-8"),
                sign_payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        return {
            "Authorization": self.token,
            "sign": signature,
            "nonce": nonce,
            "t": timestamp,
            "Content-Type": "application/json",
        }

    def get_lock_status(self) -> dict | None:
        """Get current lock status."""
        try:
            resp = requests.get(
                f"{SWITCHBOT_API_BASE}/devices/{self.device_id}/status",
                headers=self._build_headers(),
                timeout=10,
            )
            data = resp.json()
            if data.get("statusCode") == 100:
                return data["body"]
            logger.error(f"Lock status error: {data}")
            return None
        except Exception as e:
            logger.error(f"Failed to get lock status: {e}")
            return None

    def unlock(self) -> bool:
        """Send unlock command to the SwitchBot Lock."""
        try:
            resp = requests.post(
                f"{SWITCHBOT_API_BASE}/devices/{self.device_id}/commands",
                headers=self._build_headers(),
                json={
                    "command": "unlock",
                    "parameter": "default",
                    "commandType": "command",
                },
                timeout=10,
            )
            data = resp.json()

            if data.get("statusCode") == 100:
                logger.info("Lock unlocked successfully")
                return True

            logger.error(f"Unlock failed: {data}")
            return False
        except Exception as e:
            logger.error(f"Unlock request failed: {e}")
            return False

    def lock(self) -> bool:
        """Send lock command to the SwitchBot Lock."""
        try:
            resp = requests.post(
                f"{SWITCHBOT_API_BASE}/devices/{self.device_id}/commands",
                headers=self._build_headers(),
                json={
                    "command": "lock",
                    "parameter": "default",
                    "commandType": "command",
                },
                timeout=10,
            )
            data = resp.json()

            if data.get("statusCode") == 100:
                logger.info("Lock locked successfully")
                return True

            logger.error(f"Lock failed: {data}")
            return False
        except Exception as e:
            logger.error(f"Lock request failed: {e}")
            return False
