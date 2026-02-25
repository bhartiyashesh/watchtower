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
from object_detector import Detection, ObjectDetector


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


# ---------------------------------------------------------------------------
# PIPE-02: Full pipeline flow tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_motion_event(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-02: Full pipeline writes an event record for a motion event with all
    required fields: recording_id, event_type, person_name, thumbnail_path.
    """
    # First call returns a motion event; second call raises CancelledError to exit the loop
    mock_ring.wait_for_event.side_effect = [(12345, "motion"), asyncio.CancelledError()]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = None  # Stranger — no face match

    try:
        await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
    except asyncio.CancelledError:
        pass

    events = await store.get_recent_events(limit=10)
    assert len(events) == 1
    assert events[0]["recording_id"] == "12345"
    assert events[0]["event_type"] == "motion"
    assert events[0]["person_name"] is None  # Stranger
    assert events[0]["thumbnail_path"] is not None  # Thumbnail was saved


@pytest.mark.asyncio
async def test_pipeline_event_has_detections(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-02: Pipeline writes a detections list to the event record.
    With a synthetic gray frame YOLO may detect 0 objects — the key assertion
    is that the detections key exists and is a list (even if empty).
    """
    mock_ring.wait_for_event.side_effect = [(54321, "motion"), asyncio.CancelledError()]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = None

    try:
        await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
    except asyncio.CancelledError:
        pass

    event = await store.get_event(1)
    assert event is not None
    assert isinstance(event["detections"], list)


@pytest.mark.asyncio
async def test_pipeline_with_face_match(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-02 / PIPE-03: When face recognition identifies a known person, the event
    record has person_name set, unlock_granted=1, door_action='unlocked', and
    SwitchBot.unlock() was called.

    Note: We patch detector.detect to return a synthetic person Detection so that
    the pipeline calls recognizer.identify. With a flat gray test frame YOLO
    produces no detections; patching isolates the pipeline logic from YOLO accuracy.
    """
    mock_ring.wait_for_event.side_effect = [(99999, "motion"), asyncio.CancelledError()]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = "alice"

    # Patch detector.detect to return a person detection so face recognition runs
    fake_person = Detection(
        label="person", confidence=0.95,
        bbox_x1=100.0, bbox_y1=50.0, bbox_x2=300.0, bbox_y2=400.0,
    )
    with patch.object(detector, "detect", new=AsyncMock(return_value=[fake_person])):
        try:
            await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
        except asyncio.CancelledError:
            pass

    event = await store.get_event(1)
    assert event is not None
    assert event["person_name"] == "alice"
    assert event["unlock_granted"] == 1  # SQLite integer boolean
    assert event["door_action"] == "unlocked"
    assert mock_switchbot.unlock.called  # SwitchBot.unlock() was invoked


# ---------------------------------------------------------------------------
# PIPE-03: Unlock behavior preservation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_behavior_preserved(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-03: SwitchBot.unlock() called exactly once per face match event,
    and the event record correctly reflects the unlock outcome.
    """
    mock_ring.wait_for_event.side_effect = [(77777, "motion"), asyncio.CancelledError()]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = "bob"

    fake_person = Detection(
        label="person", confidence=0.92,
        bbox_x1=80.0, bbox_y1=40.0, bbox_x2=280.0, bbox_y2=380.0,
    )
    with patch.object(detector, "detect", new=AsyncMock(return_value=[fake_person])):
        try:
            await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
        except asyncio.CancelledError:
            pass

    assert mock_switchbot.unlock.call_count == 1

    event = await store.get_event(1)
    assert event["door_action"] == "unlocked"
    assert event["unlock_granted"] == 1


@pytest.mark.asyncio
async def test_cooldown_blocks_repeat_unlock(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-03: A second face-match event within the cooldown window does NOT
    trigger a second unlock. SwitchBot.unlock() must be called exactly once.

    Note: The polling_loop implementation falls through the cooldown check at
    event receipt (logs warning, no continue) and performs the second cooldown
    check just before switchbot.unlock(). Both events ARE written to EventStore;
    the second event has unlock_granted=0 because cooldown blocks the actual unlock.
    """
    # Call 1: first event; Call 2: second event within cooldown; Call 3: exit
    mock_ring.wait_for_event.side_effect = [
        (11111, "motion"),
        (22222, "motion"),
        asyncio.CancelledError(),
    ]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = "charlie"

    fake_person = Detection(
        label="person", confidence=0.90,
        bbox_x1=60.0, bbox_y1=30.0, bbox_x2=260.0, bbox_y2=360.0,
    )

    # Set a very long cooldown so the second event is always within the cooldown window
    original_cooldown = Config.UNLOCK_COOLDOWN
    Config.UNLOCK_COOLDOWN = 9999
    try:
        with patch.object(detector, "detect", new=AsyncMock(return_value=[fake_person])):
            try:
                await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
            except asyncio.CancelledError:
                pass
    finally:
        Config.UNLOCK_COOLDOWN = original_cooldown

    # Only one unlock should have been dispatched (second event was in cooldown)
    assert mock_switchbot.unlock.call_count == 1, (
        f"Expected 1 unlock call, got {mock_switchbot.unlock.call_count} — "
        "cooldown should block the second unlock"
    )

    # Both events are written (cooldown does not skip EventStore write)
    events = await store.get_recent_events(limit=10)
    assert len(events) == 2, f"Expected 2 event records, got {len(events)}"

    # The second event (most recent first) should have unlock_granted=0
    # get_recent_events returns most-recent-first; events[0] is the 2nd event
    second_event = events[0]
    assert second_event["unlock_granted"] == 0, (
        "Second event within cooldown must have unlock_granted=0"
    )


# ---------------------------------------------------------------------------
# PIPE-04: camera_id population tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_camera_id_populated_on_every_event(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-04: Every event record has camera_id set to the default Config.CAMERA_ID
    value ('front_door'). camera_id must not be None or empty.
    """
    mock_ring.wait_for_event.side_effect = [(33333, "motion"), asyncio.CancelledError()]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = None

    try:
        await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
    except asyncio.CancelledError:
        pass

    event = await store.get_event(1)
    assert event is not None
    assert event["camera_id"] is not None
    assert event["camera_id"] != ""
    assert event["camera_id"] == "front_door"  # Default Config.CAMERA_ID value


@pytest.mark.asyncio
async def test_camera_id_from_config(mock_ring, mock_switchbot, mock_recognizer, detector, store):
    """
    PIPE-04: camera_id is configurable via Config.CAMERA_ID — events written
    with a different camera_id value reflect that configuration.
    """
    mock_ring.wait_for_event.side_effect = [(44444, "motion"), asyncio.CancelledError()]
    mock_ring.capture_frame.return_value = make_test_frame()
    mock_recognizer.identify.return_value = None

    original_camera_id = Config.CAMERA_ID
    Config.CAMERA_ID = "back_patio"
    try:
        try:
            await polling_loop(mock_ring, mock_recognizer, mock_switchbot, detector, store)
        except asyncio.CancelledError:
            pass
    finally:
        Config.CAMERA_ID = original_camera_id

    event = await store.get_event(1)
    assert event is not None
    assert event["camera_id"] == "back_patio"
