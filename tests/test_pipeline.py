"""
Comprehensive test suite for Phase 3 Pipeline Integration.

Validates all PIPE requirements (PIPE-01 through PIPE-04):
  PIPE-01: FastAPI lifespan starts Ring polling as asyncio.Task
  PIPE-02: Full pipeline: Ring capture -> YOLO -> face recognition -> EventStore write
  PIPE-03: Unlock behavior preserved (face match -> SwitchBot -> cooldown)
  PIPE-04: camera_id populated on every event, configurable via env var

Mocking strategy:
  - RingClient: AsyncMock (wait_for_event, capture_frame, authenticate, stop)
  - SwitchBotClient: MagicMock (unlock — sync method dispatched via executor)
  - FaceRecognizer: MagicMock (identify — sync method dispatched via executor)
  - ObjectDetector: Real instance (module-scoped, same pattern as test_object_detector.py)
  - EventStore: Real instance in temp directory (same pattern as test_event_store.py)
"""

import asyncio
import io
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from PIL import Image

from app import app
from config import Config
from event_store import EventStore
from main import polling_loop
from object_detector import ObjectDetector


# ---------------------------------------------------------------------------
# Helper: create synthetic JPEG frame
# ---------------------------------------------------------------------------


def make_test_frame(width: int = 640, height: int = 480) -> bytes:
    """Create a synthetic JPEG frame for testing."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store():
    """Yields an initialized EventStore in a temp directory, cleans up after."""
    with tempfile.TemporaryDirectory() as td:
        s = EventStore(
            db_path=os.path.join(td, "test.db"),
            thumbnails_dir=os.path.join(td, "thumbnails"),
        )
        await s.initialize()
        yield s
        await s.close()


@pytest.fixture(scope="module")
def detector():
    """
    Instantiate ObjectDetector once per test module to avoid re-loading YOLO
    for every individual test. Shutdown is called after all tests complete.
    Module-scoped because YOLO model load is expensive (~2s).
    """
    d = ObjectDetector("yolo11n.pt")
    yield d
    d.shutdown()


@pytest.fixture
def mock_ring():
    """Mock RingClient with async methods."""
    ring = MagicMock(spec=["wait_for_event", "capture_frame", "authenticate", "stop", "doorbell"])
    ring.wait_for_event = AsyncMock()
    ring.capture_frame = AsyncMock()
    ring.authenticate = AsyncMock()
    ring.stop = AsyncMock()
    return ring


@pytest.fixture
def mock_switchbot():
    """Mock SwitchBotClient with sync unlock method."""
    switchbot = MagicMock()
    switchbot.unlock = MagicMock(return_value=True)
    return switchbot


@pytest.fixture
def mock_recognizer():
    """Mock FaceRecognizer with sync identify method returning None (no face match)."""
    recognizer = MagicMock()
    recognizer.identify = MagicMock(return_value=None)
    recognizer.known_encodings = ["fake"]  # Non-empty so lifespan doesn't warn
    return recognizer


# ---------------------------------------------------------------------------
# PIPE-01: FastAPI lifespan tests
# ---------------------------------------------------------------------------


def test_app_has_lifespan():
    """PIPE-01: FastAPI app is configured with a lifespan context manager."""
    assert app.router.lifespan_context is not None


def test_health_endpoint():
    """PIPE-01: /health endpoint route is registered on the app."""
    routes = [r.path for r in app.routes]
    assert "/health" in routes


def test_no_asyncio_run_in_source():
    """PIPE-01: Neither app.py nor main.py contains asyncio.run( — uvicorn owns the event loop."""
    base = "/Users/yasheshbharti/Documents/experiments/Ring-Auth/files/smart-lock-system"
    with open(os.path.join(base, "app.py")) as f:
        app_source = f.read()
    with open(os.path.join(base, "main.py")) as f:
        main_source = f.read()

    assert "asyncio.run(" not in app_source, "app.py must not contain asyncio.run("
    assert "asyncio.run(" not in main_source, "main.py must not contain asyncio.run("
