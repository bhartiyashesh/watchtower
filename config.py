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
        return errors
