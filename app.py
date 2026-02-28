"""
Smart Lock System — FastAPI Entry Point
========================================
Replaces the standalone asyncio.run + main() pattern with a proper FastAPI
application. The lifespan context manager initializes all services and starts
the Ring polling loop as an asyncio.Task, so uvicorn owns the event loop.

Dual-mode startup:
  - SETUP MODE: If config is incomplete, only the setup wizard is served.
  - OPERATIONAL MODE: Full service initialization + polling loop.

Usage:
    python app.py
    # or
    uvicorn app:app --host 127.0.0.1 --port 1847
"""

import asyncio
import logging
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from _paths import _is_frozen, RUNTIME_DIR
from config import Config
from dashboard.router import router as dashboard_router
from setup.router import router as setup_router

# When running as a frozen executable, change to the runtime directory so any
# stray relative paths (e.g. from third-party libs) resolve next to the exe.
if _is_frozen():
    os.chdir(RUNTIME_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smart-lock.app")


WATCHTOWER_HOSTNAME = "watchtower.look"


def _hostname_resolves() -> bool:
    """Return True if WATCHTOWER_HOSTNAME resolves to an IP."""
    try:
        socket.getaddrinfo(WATCHTOWER_HOSTNAME, None)
        return True
    except socket.gaierror:
        return False


def _offer_hosts_setup(port: int) -> None:
    """If watchtower.look isn't in /etc/hosts, ask the user to add it now."""
    if _hostname_resolves():
        return

    print(f"  '{WATCHTOWER_HOSTNAME}' is not in your hosts file.")
    print(f"  Adding it lets you access WatchTower at http://{WATCHTOWER_HOSTNAME}:{port}")
    print()

    try:
        answer = input("  Add it now? (requires sudo password) [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer in ("", "y", "yes"):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_hosts.py")
        if _is_frozen():
            script = os.path.join(RUNTIME_DIR, "setup_hosts.py")
        result = subprocess.run(
            ["sudo", sys.executable, script],
            stdin=sys.stdin,
        )
        if result.returncode == 0:
            print()
        else:
            print("  Failed to update hosts file. You can try manually later:")
            print(f"  sudo python setup_hosts.py")
            print()
    else:
        print()
        print("  Skipped. You can add it later with: sudo python setup_hosts.py")
        print()


def _print_banner(host: str, port: int, setup: bool = False) -> None:
    """Print a startup banner with access URLs."""
    local_url = f"http://{host}:{port}"
    friendly_url = f"http://{WATCHTOWER_HOSTNAME}:{port}"
    has_hostname = _hostname_resolves()
    path = "/setup/" if setup else "/dashboard/"

    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║              WatchTower is running               ║")
    print("  ╠══════════════════════════════════════════════════╣")
    print(f"  ║  Local:    {(local_url + path):<39s}║")
    if has_hostname:
        print(f"  ║  Network:  {(friendly_url + path):<39s}║")
    print("  ╠══════════════════════════════════════════════════╣")
    if setup:
        print("  ║  Complete setup to start monitoring.             ║")
    else:
        print("  ║  Dashboard is live. Happy monitoring!            ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    In SETUP MODE (config incomplete): no services are initialized — only the
    setup wizard routes are active.

    In OPERATIONAL MODE: validates config, initializes all services, and starts
    the Ring polling loop as an asyncio.Task. On shutdown, cancels the polling
    task and cleanly closes all resources.
    """
    host = Config.FASTAPI_HOST
    port = Config.FASTAPI_PORT

    _offer_hosts_setup(port)

    if not Config.is_configured():
        # --- SETUP MODE ---
        logger.info("Configuration incomplete — starting in setup mode.")
        _print_banner(host, port, setup=True)
        app.state.setup_mode = True
        yield
        return

    # --- OPERATIONAL MODE ---
    app.state.setup_mode = False

    from ring_client import RingClient
    from face_recognizer import FaceRecognizer
    from switchbot_client import SwitchBotClient
    from object_detector import ObjectDetector
    from event_store import EventStore
    from telegram_alerter import TelegramAlerter
    from telegram_commands import TelegramCommandHandler
    from main import polling_loop

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

    # --- Initialize BlinkClient (optional — standalone camera feed) ---
    blink = None
    if Config.BLINK_USERNAME and Config.BLINK_PASSWORD:
        from blink_client import BlinkClient
        logger.info("Initializing Blink camera client...")
        blink = BlinkClient(
            Config.BLINK_USERNAME,
            Config.BLINK_PASSWORD,
            Config.BLINK_CAMERA_NAME,
        )
        try:
            await blink.authenticate()
            if blink.needs_2fa:
                logger.info("Blink awaiting 2FA — submit code via dashboard")
        except Exception:
            logger.exception("Blink authentication failed — camera feed disabled")
            blink = None
    else:
        logger.info("BLINK_USERNAME/BLINK_PASSWORD not set — Blink camera feed disabled")

    # Expose services on app.state for dashboard routes
    app.state.store = store
    app.state.alerter = alerter
    app.state.switchbot = switchbot
    app.state.ring = ring
    app.state.recognizer = recognizer
    app.state.detector = detector
    app.state.blink = blink

    _print_banner(host, port)

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

    if blink is not None:
        await blink.stop()

    detector.shutdown()
    await store.close()
    await ring.stop()

    logger.info("Shutdown complete.")


app = FastAPI(lifespan=lifespan, title="Smart Lock System")
app.include_router(setup_router)
app.include_router(dashboard_router)


@app.middleware("http")
async def setup_mode_guard(request: Request, call_next):
    """In setup mode, redirect all non-setup/non-health routes to the wizard."""
    if getattr(request.app.state, "setup_mode", False):
        path = request.url.path
        if not path.startswith("/setup") and path != "/health":
            return RedirectResponse("/setup/", status_code=303)
    return await call_next(request)


@app.get("/")
async def root():
    """Redirect root to dashboard."""
    return RedirectResponse("/dashboard/", status_code=303)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app:app", host=Config.FASTAPI_HOST, port=Config.FASTAPI_PORT, log_level="info")
