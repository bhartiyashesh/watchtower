import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Ring
    RING_USERNAME: str = os.getenv("RING_USERNAME", "")
    RING_PASSWORD: str = os.getenv("RING_PASSWORD", "")

    # SwitchBot
    SWITCHBOT_TOKEN: str = os.getenv("SWITCHBOT_TOKEN", "")
    SWITCHBOT_SECRET: str = os.getenv("SWITCHBOT_SECRET", "")
    SWITCHBOT_DEVICE_ID: str = os.getenv("SWITCHBOT_DEVICE_ID", "")

    # Face Recognition
    FACE_MATCH_TOLERANCE: float = float(os.getenv("FACE_MATCH_TOLERANCE", "0.5"))
    KNOWN_FACES_DIR: Path = Path(os.getenv("KNOWN_FACES_DIR", "./known_faces"))

    # Timing
    POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL", "5"))
    UNLOCK_COOLDOWN: int = int(os.getenv("UNLOCK_COOLDOWN", "60"))

    # Storage
    DB_PATH: str = os.getenv("DB_PATH", "./events.db")
    THUMBNAILS_DIR: str = os.getenv("THUMBNAILS_DIR", "./thumbnails")

    # YOLO Object Detection
    YOLO_MODEL_PATH: str = os.getenv("YOLO_MODEL_PATH", "yolo11n.pt")

    # Pipeline (Phase 3)
    CAMERA_ID: str = os.getenv("CAMERA_ID", "front_door")
    FASTAPI_HOST: str = os.getenv("FASTAPI_HOST", "127.0.0.1")
    FASTAPI_PORT: int = int(os.getenv("FASTAPI_PORT", "8000"))

    # Telegram Alerts (Phase 4)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Web Dashboard (Phase 5)
    DASHBOARD_USERNAME: str = os.getenv("DASHBOARD_USERNAME", "admin")
    DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_PASSWORD", "")

    # Analytics (Phase 6) — fallback coordinates when browser geolocation denied
    ANALYTICS_LATITUDE: str = os.getenv("ANALYTICS_LATITUDE", "37.7749")
    ANALYTICS_LONGITUDE: str = os.getenv("ANALYTICS_LONGITUDE", "-122.4194")

    @classmethod
    def validate(cls) -> list[str]:
        errors = []
        if not cls.RING_USERNAME:
            errors.append("RING_USERNAME is required")
        if not cls.RING_PASSWORD:
            errors.append("RING_PASSWORD is required")
        if not cls.SWITCHBOT_TOKEN:
            errors.append("SWITCHBOT_TOKEN is required")
        if not cls.SWITCHBOT_SECRET:
            errors.append("SWITCHBOT_SECRET is required")
        if not cls.SWITCHBOT_DEVICE_ID:
            errors.append("SWITCHBOT_DEVICE_ID is required")
        if not cls.KNOWN_FACES_DIR.exists():
            errors.append(f"Known faces directory not found: {cls.KNOWN_FACES_DIR}")
        if not cls.DASHBOARD_PASSWORD:
            errors.append("DASHBOARD_PASSWORD is required")
        return errors

    @classmethod
    def is_configured(cls) -> bool:
        """Return True if all required config is present (no validation errors)."""
        return len(cls.validate()) == 0

    @classmethod
    def reload(cls):
        """Re-read .env and reassign all class variables."""
        load_dotenv(override=True)
        cls.RING_USERNAME = os.getenv("RING_USERNAME", "")
        cls.RING_PASSWORD = os.getenv("RING_PASSWORD", "")
        cls.SWITCHBOT_TOKEN = os.getenv("SWITCHBOT_TOKEN", "")
        cls.SWITCHBOT_SECRET = os.getenv("SWITCHBOT_SECRET", "")
        cls.SWITCHBOT_DEVICE_ID = os.getenv("SWITCHBOT_DEVICE_ID", "")
        cls.FACE_MATCH_TOLERANCE = float(os.getenv("FACE_MATCH_TOLERANCE", "0.5"))
        cls.KNOWN_FACES_DIR = Path(os.getenv("KNOWN_FACES_DIR", "./known_faces"))
        cls.POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
        cls.UNLOCK_COOLDOWN = int(os.getenv("UNLOCK_COOLDOWN", "60"))
        cls.DB_PATH = os.getenv("DB_PATH", "./events.db")
        cls.THUMBNAILS_DIR = os.getenv("THUMBNAILS_DIR", "./thumbnails")
        cls.YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolo11n.pt")
        cls.CAMERA_ID = os.getenv("CAMERA_ID", "front_door")
        cls.FASTAPI_HOST = os.getenv("FASTAPI_HOST", "127.0.0.1")
        cls.FASTAPI_PORT = int(os.getenv("FASTAPI_PORT", "8000"))
        cls.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        cls.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
        cls.DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
        cls.DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
        cls.ANALYTICS_LATITUDE = os.getenv("ANALYTICS_LATITUDE", "37.7749")
        cls.ANALYTICS_LONGITUDE = os.getenv("ANALYTICS_LONGITUDE", "-122.4194")
