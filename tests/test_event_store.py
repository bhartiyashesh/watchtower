"""
Comprehensive test suite for EventStore.

Validates all DATA requirements (DATA-01 through DATA-06):
  DATA-01: Event writes and reads (motion, ding, stranger)
  DATA-02: Detection storage with bbox and label preservation
  DATA-03: Thumbnail save and failure isolation
  DATA-04: Unlock action and door action logging
  DATA-05: Concurrent read/write without "database is locked" errors
  DATA-06: Separate face_distance and face_confidence columns

All tests create a fresh EventStore in a temporary directory so they are
fully isolated from each other and from the production database.
"""

import asyncio
import io
import os
import sqlite3
import tempfile

import pytest
import pytest_asyncio
from PIL import Image

from event_store import EventStore


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


# ---------------------------------------------------------------------------
# DATA-05: WAL mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wal_mode_active(store):
    """DATA-05: Confirm journal_mode is WAL (not DELETE/TRUNCATE)."""
    async with store.db.execute("PRAGMA journal_mode") as cursor:
        row = await cursor.fetchone()
    assert row[0] == "wal", f"Expected 'wal' but got '{row[0]}'"


# ---------------------------------------------------------------------------
# DATA-01: Basic event write and read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_and_read_motion_event(store):
    """DATA-01: Write a motion event and read it back with all fields intact."""
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:30:00",
        event_type="motion",
        recording_id="rec_001",
    )

    event = await store.get_event(event_id)

    assert event is not None
    assert event["camera_id"] == "front_door"
    assert event["recorded_at"] == "2026-02-25T10:30:00"
    assert event["event_type"] == "motion"
    assert event["recording_id"] == "rec_001"
    assert event["created_at"] is not None


# ---------------------------------------------------------------------------
# DATA-02: Detection storage and order preservation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_and_read_detections(store):
    """DATA-02: Three detections stored and retrieved in insertion order."""
    detections = [
        {"label": "person", "confidence": 0.95, "bbox_x1": 10, "bbox_y1": 20, "bbox_x2": 100, "bbox_y2": 200},
        {"label": "dog", "confidence": 0.87},
        {"label": "car", "confidence": 0.72, "bbox_x1": 200, "bbox_y1": 50, "bbox_x2": 400, "bbox_y2": 300},
    ]
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:31:00",
        detections=detections,
    )

    event = await store.get_event(event_id)

    assert len(event["detections"]) == 3
    labels = [d["label"] for d in event["detections"]]
    assert labels == ["person", "dog", "car"]

    person = event["detections"][0]
    assert person["bbox_x1"] == 10
    assert person["bbox_y1"] == 20
    assert person["bbox_x2"] == 100
    assert person["bbox_y2"] == 200

    dog = event["detections"][1]
    assert dog["bbox_x1"] is None
    assert dog["bbox_y1"] is None
    assert dog["bbox_x2"] is None
    assert dog["bbox_y2"] is None


# ---------------------------------------------------------------------------
# DATA-03: Thumbnail save and path storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thumbnail_save_and_path_storage(store):
    """DATA-03: Save a valid JPEG thumbnail, confirm it exists on disk."""
    # Create a minimal valid JPEG in memory
    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    jpeg_bytes = buf.getvalue()

    path = store.save_thumbnail(jpeg_bytes, "2026-02-25T10-30-00")

    assert path is not None
    assert "thumbnails" in path
    assert path.endswith(".jpg")

    # File must actually be on disk
    assert os.path.exists(path), f"Thumbnail not found at {path}"
    assert os.path.getsize(path) > 0

    # Event with thumbnail path stored and read back
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:30:00",
        thumbnail_path=path,
    )
    event = await store.get_event(event_id)
    assert event["thumbnail_path"] == path


# ---------------------------------------------------------------------------
# DATA-04: Unlock action logging
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unlock_action_logged(store):
    """DATA-04: Unlock fields (person_name, face_confidence, unlock_granted, door_action) persisted correctly."""
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:32:00",
        person_name="alice",
        face_confidence=0.85,
        unlock_granted=True,
        door_action="unlocked",
    )

    event = await store.get_event(event_id)

    assert event["unlock_granted"] == 1
    assert event["door_action"] == "unlocked"
    assert event["person_name"] == "alice"
    assert event["face_confidence"] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# DATA-06: Separate face_distance and face_confidence columns
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_face_recognition_results(store):
    """DATA-06: face_distance and face_confidence are separate columns with distinct values."""
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:33:00",
        person_name="bob",
        face_distance=0.32,
        face_confidence=0.68,
    )

    event = await store.get_event(event_id)

    assert event["face_distance"] == pytest.approx(0.32)
    assert event["face_confidence"] == pytest.approx(0.68)
    assert event["person_name"] == "bob"
    # Verify they are genuinely distinct values
    assert event["face_distance"] != event["face_confidence"]


# ---------------------------------------------------------------------------
# DATA-01, DATA-06: Stranger event (person_name=None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stranger_event(store):
    """DATA-01, DATA-06: Stranger events (person_name=None) stored and distinguishable."""
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:34:00",
        person_name=None,
        face_confidence=None,
        face_distance=None,
        unlock_granted=False,
    )

    event = await store.get_event(event_id)

    assert event["person_name"] is None
    assert event["face_confidence"] is None
    assert event["unlock_granted"] == 0


# ---------------------------------------------------------------------------
# DATA-01: Event with no detections (NULL detections case)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_with_no_detections(store):
    """DATA-01: Event written with detections=None returns empty list, not None."""
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:35:00",
        detections=None,
    )

    event = await store.get_event(event_id)

    assert event["detections"] == []


# ---------------------------------------------------------------------------
# DATA-05: Concurrent read/write (critical — proves WAL works under load)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_read_write(store):
    """DATA-05: 20 concurrent writes + 20 concurrent reads complete without 'database is locked'."""
    exceptions: list[Exception] = []

    async def writer():
        for i in range(20):
            try:
                await store.write_event(
                    camera_id="front_door",
                    recorded_at=f"2026-02-25T10:{i:02d}:00",
                )
            except Exception as exc:
                exceptions.append(exc)
            await asyncio.sleep(0.01)

    async def reader():
        for _ in range(20):
            try:
                await store.get_recent_events(limit=5)
            except Exception as exc:
                exceptions.append(exc)
            await asyncio.sleep(0.01)

    await asyncio.gather(writer(), reader())

    assert exceptions == [], f"Concurrent access raised exceptions: {exceptions}"

    all_events = await store.get_recent_events(limit=100)
    assert len(all_events) == 20


# ---------------------------------------------------------------------------
# DATA-03: Thumbnail failure returns None (graceful failure)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thumbnail_save_failure_returns_none(store):
    """DATA-03: Invalid bytes return None — no exception, no event blocked."""
    result = store.save_thumbnail(b"not_a_valid_jpeg", "2026-02-25T11-00-00")
    assert result is None


# ---------------------------------------------------------------------------
# Pagination (supports DASH-06 readiness)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_recent_events_pagination(store):
    """Pagination via limit/offset: 10 events → batches of 3, last page returns 1."""
    for i in range(10):
        await store.write_event(
            camera_id="front_door",
            recorded_at=f"2026-02-25T10:{i:02d}:00",
        )

    page1 = await store.get_recent_events(limit=3, offset=0)
    assert len(page1) == 3

    page2 = await store.get_recent_events(limit=3, offset=3)
    assert len(page2) == 3

    page4 = await store.get_recent_events(limit=3, offset=9)
    assert len(page4) == 1

    # Most recent event should come first (offset=0, limit=1)
    first = await store.get_recent_events(limit=1, offset=0)
    assert first[0]["recorded_at"] == "2026-02-25T10:09:00"


# ---------------------------------------------------------------------------
# DATA-01: Ding event type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ding_event_type(store):
    """DATA-01: 'ding' event_type stored and retrieved without corruption."""
    event_id = await store.write_event(
        camera_id="front_door",
        recorded_at="2026-02-25T10:36:00",
        event_type="ding",
    )

    event = await store.get_event(event_id)

    assert event["event_type"] == "ding"


# ---------------------------------------------------------------------------
# Schema integrity: foreign key enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_foreign_key_enforcement(store):
    """PRAGMA foreign_keys=ON is active: inserting a detection with a fake event_id raises IntegrityError."""
    import aiosqlite

    # Attempt to insert a detection referencing a non-existent event (event_id=9999)
    with pytest.raises(aiosqlite.IntegrityError):
        await store.db.execute(
            "INSERT INTO detections (event_id, label, confidence) VALUES (?, ?, ?)",
            (9999, "person", 0.99),
        )
        await store.db.commit()


# ---------------------------------------------------------------------------
# Schema integrity: persons table UNIQUE constraint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persons_table_unique_constraint(store):
    """Persons table enforces UNIQUE(name): inserting duplicate name raises IntegrityError."""
    import aiosqlite

    # First insert should succeed
    await store.db.execute(
        "INSERT INTO persons (name, display_name) VALUES (?, ?)",
        ("carol", "Carol"),
    )
    await store.db.commit()

    # Second insert with same name must raise IntegrityError
    with pytest.raises(aiosqlite.IntegrityError):
        await store.db.execute(
            "INSERT INTO persons (name, display_name) VALUES (?, ?)",
            ("carol", "Carol Duplicate"),
        )
        await store.db.commit()
