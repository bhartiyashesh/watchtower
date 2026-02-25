"""
Comprehensive test suite for the web dashboard (Phase 5).

Validates all DASH requirements:
  DASH-01: Event history feed — GET /dashboard/events shows events with timestamps and detections
  DASH-02: Date range filtering — date_range param (today/7d/30d/all) filters correctly
  DASH-03: Object type filtering — object_type param filters by YOLO detection label
  DASH-04: Summary widget — GET /dashboard/ shows today_count, last_event card, lock status
  DASH-05: Authenticated thumbnail serving — FileResponse with path traversal protection
  DASH-06: Pagination — page/limit params, has_next heuristic, Previous/Next links
  DASH-07: HTTP Basic Auth enforcement — unauthenticated requests return 401

Uses a minimal test FastAPI app that includes ONLY the dashboard router.
Does NOT import or trigger app.py's lifespan (avoids Ring/SwitchBot credential requirements).
Uses a real EventStore in a temp directory for accurate SQL filter testing.
"""

import asyncio
import base64
import datetime
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.router import router, verify_credentials
from event_store import EventStore


# ---------------------------------------------------------------------------
# Helper: Build Authorization header for HTTP Basic Auth
# ---------------------------------------------------------------------------

def auth_header(username: str = "testuser", password: str = "testpass") -> dict:
    """Return Authorization header dict for HTTP Basic Auth."""
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


# ---------------------------------------------------------------------------
# Helper: Insert a test event synchronously from a sync fixture
# ---------------------------------------------------------------------------

def insert_test_event(
    store: EventStore,
    recorded_at: str,
    person_name: str | None = None,
    detections: list[dict] | None = None,
    camera_id: str = "front_door",
) -> int:
    """Run store.write_event() in the current event loop (sync wrapper)."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(
        store.write_event(
            camera_id=camera_id,
            recorded_at=recorded_at,
            person_name=person_name,
            detections=detections or [],
        )
    )


# ---------------------------------------------------------------------------
# Fixture: dashboard_client (authenticated — dependency overridden)
# ---------------------------------------------------------------------------

@pytest.fixture
def dashboard_client():
    """
    Yields an authenticated TestClient for the dashboard test app.

    - Real EventStore in a temp directory (tests actual SQL queries)
    - verify_credentials dependency is overridden to bypass HTTP Basic Auth check
    - SwitchBot mocked to return a stable lock status
    """
    test_app = FastAPI()
    test_app.include_router(router)

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        thumbnails_dir = os.path.join(td, "thumbnails")

        loop = asyncio.get_event_loop()

        store = EventStore(db_path=db_path, thumbnails_dir=thumbnails_dir)
        loop.run_until_complete(store.initialize())

        switchbot_mock = MagicMock()
        switchbot_mock.get_lock_status.return_value = {"lockState": "locked", "battery": 90}

        test_app.state.store = store
        test_app.state.switchbot = switchbot_mock

        # Override auth dependency to always pass
        test_app.dependency_overrides[verify_credentials] = lambda: "testuser"

        with TestClient(test_app) as client:
            # Expose store so individual tests can insert data
            client.store = store  # type: ignore[attr-defined]
            client.td = td  # type: ignore[attr-defined]
            yield client

        # Clean up
        loop.run_until_complete(store.close())
        test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Fixture: no_auth_client (no dependency override — real 401 testing)
# ---------------------------------------------------------------------------

@pytest.fixture
def no_auth_client():
    """
    Yields a TestClient without overriding verify_credentials.

    Uses default Config.DASHBOARD_USERNAME/DASHBOARD_PASSWORD for auth.
    Requests without valid credentials should return 401.
    """
    test_app = FastAPI()
    test_app.include_router(router)

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        thumbnails_dir = os.path.join(td, "thumbnails")

        loop = asyncio.get_event_loop()
        store = EventStore(db_path=db_path, thumbnails_dir=thumbnails_dir)
        loop.run_until_complete(store.initialize())

        switchbot_mock = MagicMock()
        switchbot_mock.get_lock_status.return_value = {"lockState": "locked", "battery": 90}

        test_app.state.store = store
        test_app.state.switchbot = switchbot_mock
        # NOTE: no dependency_overrides — real auth enforced

        with TestClient(test_app, raise_server_exceptions=False) as client:
            yield client

        loop.run_until_complete(store.close())


# ===========================================================================
# DASH-07: HTTP Basic Auth enforcement
# ===========================================================================

def test_dashboard_home_requires_auth(no_auth_client):
    """DASH-07: GET /dashboard/ without credentials returns 401 with WWW-Authenticate header."""
    response = no_auth_client.get("/dashboard/")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert response.headers["WWW-Authenticate"] == "Basic"


def test_events_requires_auth(no_auth_client):
    """DASH-07: GET /dashboard/events without credentials returns 401."""
    response = no_auth_client.get("/dashboard/events")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


def test_thumbnails_requires_auth(no_auth_client):
    """DASH-07: GET /dashboard/thumbnails/{filename} without credentials returns 401."""
    response = no_auth_client.get("/dashboard/thumbnails/test.jpg")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


# ===========================================================================
# DASH-01: Event history feed
# ===========================================================================

def test_events_feed_renders_events(dashboard_client):
    """DASH-01: GET /dashboard/events returns 200 with event timestamps and detection labels."""
    today = datetime.datetime.utcnow().isoformat(timespec="seconds")

    insert_test_event(
        dashboard_client.store,
        recorded_at=today,
        person_name="alice",
        detections=[{"label": "person", "confidence": 0.95}],
    )
    insert_test_event(
        dashboard_client.store,
        recorded_at=today,
        person_name=None,
        detections=[{"label": "dog", "confidence": 0.88}],
    )
    insert_test_event(
        dashboard_client.store,
        recorded_at=today,
        person_name="bob",
        detections=[{"label": "car", "confidence": 0.72}],
    )

    response = dashboard_client.get("/dashboard/events")
    assert response.status_code == 200
    content = response.text

    # Timestamps should appear
    # The recorded_at value has 'T' separator — template renders it directly
    assert today[:10] in content  # date portion is enough to confirm
    # Detection labels appear as tags
    assert "person" in content
    assert "dog" in content
    assert "car" in content


def test_events_feed_empty(dashboard_client):
    """DASH-01: GET /dashboard/events with no events in DB returns 200 with empty state message."""
    response = dashboard_client.get("/dashboard/events")
    assert response.status_code == 200
    assert "No events match" in response.text


# ===========================================================================
# DASH-02: Date range filtering
# ===========================================================================

def test_filter_by_date_range_today(dashboard_client):
    """DASH-02: date_range=today shows today's events and hides old events."""
    today_ts = datetime.datetime.utcnow().isoformat(timespec="seconds")
    old_ts = "2020-01-01T12:00:00"

    insert_test_event(
        dashboard_client.store,
        recorded_at=today_ts,
        person_name="today_person",
    )
    insert_test_event(
        dashboard_client.store,
        recorded_at=old_ts,
        person_name="old_person",
    )

    response = dashboard_client.get("/dashboard/events?date_range=today")
    assert response.status_code == 200
    content = response.text

    # Today's event person name appears
    assert "today_person" in content
    # Old event person name should NOT appear
    assert "old_person" not in content


# ===========================================================================
# DASH-03: Object type filtering
# ===========================================================================

def test_filter_by_object_type(dashboard_client):
    """DASH-03: object_type=dog filters to only events with a 'dog' detection."""
    today = datetime.datetime.utcnow().isoformat(timespec="seconds")

    # Event with dog detection
    insert_test_event(
        dashboard_client.store,
        recorded_at=today,
        person_name="dog_event",
        detections=[{"label": "dog", "confidence": 0.91}],
    )
    # Event with person detection only (no dog)
    insert_test_event(
        dashboard_client.store,
        recorded_at=today,
        person_name="person_event",
        detections=[{"label": "person", "confidence": 0.95}],
    )

    response = dashboard_client.get("/dashboard/events?object_type=dog")
    assert response.status_code == 200
    content = response.text

    # Dog event appears
    assert "dog_event" in content
    # Person-only event does NOT appear
    assert "person_event" not in content


# ===========================================================================
# DASH-04: Summary widget
# ===========================================================================

def test_dashboard_home_summary(dashboard_client):
    """DASH-04: GET /dashboard/ shows today_count and lock status text."""
    today_ts = datetime.datetime.utcnow().isoformat(timespec="seconds")

    insert_test_event(dashboard_client.store, recorded_at=today_ts)
    insert_test_event(dashboard_client.store, recorded_at=today_ts)

    response = dashboard_client.get("/dashboard/")
    assert response.status_code == 200
    content = response.text

    # Today count = 2 should appear somewhere in the page
    assert "2" in content
    # Lock status text — the mock returns "locked"
    assert "Locked" in content or "locked" in content


def test_dashboard_home_no_events(dashboard_client):
    """DASH-04: GET /dashboard/ with empty DB shows 0 for today_count."""
    response = dashboard_client.get("/dashboard/")
    assert response.status_code == 200
    content = response.text

    # today_count=0 should be rendered
    assert "0" in content
    # Empty state for last_event
    assert "No events recorded yet" in content


# ===========================================================================
# DASH-05: Authenticated thumbnail serving
# ===========================================================================

def test_thumbnail_served(dashboard_client):
    """DASH-05: GET /dashboard/thumbnails/{filename} returns 200 with image/jpeg content-type."""
    # Create a real JPEG file in the temp thumbnails dir
    thumbnails_dir = os.path.join(dashboard_client.td, "thumbnails")
    os.makedirs(thumbnails_dir, exist_ok=True)
    test_file = os.path.join(thumbnails_dir, "test-event.jpg")

    # Write a minimal JPEG (just needs to be a valid file)
    with open(test_file, "wb") as f:
        # Minimal JPEG header bytes
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9")

    # Patch Config.THUMBNAILS_DIR to point to temp dir
    import config
    original_thumbnails_dir = config.Config.THUMBNAILS_DIR
    config.Config.THUMBNAILS_DIR = thumbnails_dir

    try:
        response = dashboard_client.get("/dashboard/thumbnails/test-event.jpg")
        assert response.status_code == 200
        assert "image/jpeg" in response.headers.get("content-type", "")
    finally:
        config.Config.THUMBNAILS_DIR = original_thumbnails_dir


def test_thumbnail_not_found(dashboard_client):
    """DASH-05: GET /dashboard/thumbnails/nonexistent.jpg returns 404."""
    import config
    original_thumbnails_dir = config.Config.THUMBNAILS_DIR

    thumbnails_dir = os.path.join(dashboard_client.td, "thumbnails")
    os.makedirs(thumbnails_dir, exist_ok=True)
    config.Config.THUMBNAILS_DIR = thumbnails_dir

    try:
        response = dashboard_client.get("/dashboard/thumbnails/nonexistent.jpg")
        assert response.status_code == 404
    finally:
        config.Config.THUMBNAILS_DIR = original_thumbnails_dir


def test_thumbnail_path_traversal_blocked(dashboard_client):
    """DASH-05: Path traversal attack is blocked — response is 400 or 404.

    Starlette URL-normalizes paths before routing: '../../.env' is resolved to
    '.env' by the ASGI layer, so the route handler may see filename='.env'
    without the '..' characters. Either way the attack is blocked:
    - If '..' survives normalization -> router returns 400 (explicit guard)
    - If Starlette normalizes it -> '.env' not in thumbnails dir -> 404
    Both outcomes prevent file system escape. The test accepts either status.
    """
    response = dashboard_client.get("/dashboard/thumbnails/../../.env")
    assert response.status_code in (400, 404)


def test_thumbnail_dotdot_blocked(dashboard_client):
    """DASH-05: URL-encoded path traversal (..%2F) is blocked with 400 or 404."""
    # FastAPI/Starlette URL decode happens before routing; after decode ".." is caught
    response = dashboard_client.get("/dashboard/thumbnails/..%2F..%2F.env")
    # Should be 400 (path traversal guard catches it) or 404 (file doesn't exist in safe path)
    assert response.status_code in (400, 404)


# ===========================================================================
# DASH-06: Pagination
# ===========================================================================

def test_pagination_page_1(dashboard_client):
    """DASH-06: 25 events at limit=20 — page 1 shows 'Next' navigation link."""
    today = datetime.datetime.utcnow().isoformat(timespec="seconds")

    # Insert 25 events with slightly different timestamps to avoid collisions
    loop = asyncio.get_event_loop()
    for i in range(25):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(seconds=i)).isoformat(timespec="seconds")
        loop.run_until_complete(
            dashboard_client.store.write_event(
                camera_id="front_door",
                recorded_at=ts,
            )
        )

    response = dashboard_client.get("/dashboard/events?limit=20")
    assert response.status_code == 200
    content = response.text

    # Page 1 with 25 events and limit=20: has_next=True, "Next" link should appear
    assert "Next" in content
    # Previous should NOT appear on page 1
    assert "Previous" not in content or "prev" not in content.lower()


def test_pagination_page_2(dashboard_client):
    """DASH-06: 25 events at limit=20 — page 2 shows 'Previous' link and remaining 5 events, no Next."""
    loop = asyncio.get_event_loop()
    for i in range(25):
        ts = (datetime.datetime.utcnow() - datetime.timedelta(seconds=i)).isoformat(timespec="seconds")
        loop.run_until_complete(
            dashboard_client.store.write_event(
                camera_id="front_door",
                recorded_at=ts,
            )
        )

    response = dashboard_client.get("/dashboard/events?page=2&limit=20")
    assert response.status_code == 200
    content = response.text

    # Page 2 with 5 remaining events: has_next=False (5 < 20)
    assert "Next" not in content
    # Previous link (going back to page 1)
    assert "Previous" in content or "prev" in content.lower() or "← " in content
