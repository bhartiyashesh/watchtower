"""
Smart Lock System — FastAPI Entry Point
========================================
Replaces the standalone asyncio.run + main() pattern with a proper FastAPI
application. The lifespan context manager initializes all services and starts
the Ring polling loop as an asyncio.Task, so uvicorn owns the event loop.

Usage:
    python app.py
    # or
    uvicorn app:app --host 127.0.0.1 --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from config import Config
from ring_client import RingClient
from face_recognizer import FaceRecognizer
from switchbot_client import SwitchBotClient
from object_detector import ObjectDetector
from event_store import EventStore
from telegram_alerter import TelegramAlerter
from telegram_commands import TelegramCommandHandler
from main import polling_loop
from dashboard.router import router as dashboard_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smart-lock.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Validates config, initializes all services, and starts the Ring polling
    loop as an asyncio.Task. On shutdown, cancels the polling task and cleanly
    closes all resources.
    """
    # --- Validate config ---
    errors = Config.validate()
    if errors:
        for e in errors:
            logger.error(e)
        raise RuntimeError(f"Configuration invalid: {errors}")

    # --- Initialize EventStore ---
    logger.info("Initializing EventStore...")
    store = EventStore(Config.DB_PATH, Config.THUMBNAILS_DIR)
    await store.initialize()

    # --- Initialize ObjectDetector ---
    logger.info("Loading YOLO model...")
    detector = ObjectDetector(Config.YOLO_MODEL_PATH)

    # --- Initialize FaceRecognizer ---
    logger.info("Loading face recognition model...")
    recognizer = FaceRecognizer(Config.KNOWN_FACES_DIR, Config.FACE_MATCH_TOLERANCE)
    if not recognizer.known_encodings:
        logger.warning(
            "No known faces loaded — face recognition will not grant access. "
            "Add images to the known_faces/ directory to enable face-based unlocking."
        )

    # --- Initialize SwitchBotClient ---
    logger.info("Initializing SwitchBot client...")
    switchbot = SwitchBotClient(
        Config.SWITCHBOT_TOKEN,
        Config.SWITCHBOT_SECRET,
        Config.SWITCHBOT_DEVICE_ID,
    )

    # --- Initialize RingClient ---
    logger.info("Authenticating with Ring...")
    ring = RingClient(Config.RING_USERNAME, Config.RING_PASSWORD)
    await ring.authenticate()

    # --- Initialize Telegram alerter (optional — graceful if token missing) ---
    alerter = None
    if Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID:
        logger.info("Initializing Telegram alerter...")
        alerter = TelegramAlerter(
            token=Config.TELEGRAM_BOT_TOKEN,
            chat_id=Config.TELEGRAM_CHAT_ID,
        )
        await alerter.initialize()
    else:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — "
            "Telegram alerts disabled"
        )

    # --- Start polling loop as asyncio.Task ---
    logger.info(
        f"Starting polling loop (interval={Config.POLL_INTERVAL}s, "
        f"camera_id={Config.CAMERA_ID})..."
    )
    polling_task = asyncio.create_task(
        polling_loop(ring, recognizer, switchbot, detector, store, alerter)
    )

    # --- Start Telegram command handler (if alerter is available) ---
    command_task = None
    if alerter is not None:
        logger.info("Starting Telegram command handler...")
        command_handler = TelegramCommandHandler(
            alerter=alerter,
            switchbot=switchbot,
            store=store,
            ring=ring,
            chat_id=Config.TELEGRAM_CHAT_ID,
        )
        command_task = asyncio.create_task(command_handler.run())

    # Expose services on app.state for Phase 5 dashboard routes
    app.state.store = store
    app.state.alerter = alerter
    app.state.switchbot = switchbot
    app.state.ring = ring

    yield

    # --- Graceful shutdown ---
    logger.info("Shutting down Smart Lock System...")

    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    if command_task is not None:
        command_task.cancel()
        try:
            await command_task
        except asyncio.CancelledError:
            pass

    if alerter is not None:
        await alerter.shutdown()

    detector.shutdown()
    await store.close()
    await ring.stop()

    logger.info("Shutdown complete.")


app = FastAPI(lifespan=lifespan, title="Smart Lock System")
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app:app", host=Config.FASTAPI_HOST, port=Config.FASTAPI_PORT, log_level="info")
