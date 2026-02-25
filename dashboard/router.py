"""
Dashboard router for Smart Lock System web interface.

Provides HTTP Basic Auth-protected routes for:
- /dashboard/         — Summary widget (today's count, last event, lock status)
- /dashboard/events   — Event feed with date range and object type filters + pagination
- /dashboard/thumbnails/{filename} — Authenticated JPEG thumbnail serving via FileResponse

Security:
- All routes require HTTP Basic Auth via router-level Depends(verify_credentials)
- Credentials compared with secrets.compare_digest() (timing-safe, prevents timing attacks)
- Thumbnails served via FileResponse (NOT StaticFiles) to enforce per-request auth
- Path traversal prevented: filenames containing "/" or ".." are rejected with 400
- Jinja2 auto-escaping is ON by default (XSS protection for person names and labels)
"""

import asyncio
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from config import Config

# ---------------------------------------------------------------------------
# HTTP Basic Auth security scheme
# ---------------------------------------------------------------------------
security = HTTPBasic()


def verify_credentials(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    """
    Verify HTTP Basic Auth credentials using timing-safe comparison.

    Compares against Config.DASHBOARD_USERNAME and Config.DASHBOARD_PASSWORD.
    Uses secrets.compare_digest() to prevent timing-based side-channel attacks.

    Returns:
        The authenticated username string.

    Raises:
        HTTPException 401 with WWW-Authenticate header if credentials are invalid.
    """
    correct_username = Config.DASHBOARD_USERNAME.encode("utf-8")
    correct_password = Config.DASHBOARD_PASSWORD.encode("utf-8")

    provided_username = credentials.username.encode("utf-8")
    provided_password = credentials.password.encode("utf-8")

    username_ok = secrets.compare_digest(provided_username, correct_username)
    password_ok = secrets.compare_digest(provided_password, correct_password)

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


# ---------------------------------------------------------------------------
# Router setup — router-level dependency enforces auth on ALL routes
# including /dashboard/thumbnails/{filename}
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/dashboard", dependencies=[Depends(verify_credentials)])

templates = Jinja2Templates(directory="dashboard/templates")

# Add basename filter so templates can use {{ event.thumbnail_path | basename }}
# EventStore.save_thumbnail() returns paths like "thumbnails/2026-02-25T10-30-00.jpg"
# The /dashboard/thumbnails/ route expects just the filename component
templates.env.filters["basename"] = lambda path: path.split("/")[-1] if path else ""


# ---------------------------------------------------------------------------
# Route: GET /dashboard/ — Summary widget (DASH-04)
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request) -> HTMLResponse:
    """
    Dashboard summary view.

    Displays:
    - Today's total event count (via EventStore.get_today_event_count)
    - Most recent event card with thumbnail, timestamp, and person name
    - Current lock status (via SwitchBotClient.get_lock_status in thread executor)
    """
    store = request.app.state.store
    switchbot = request.app.state.switchbot

    # Today's event count
    today_count: int = await store.get_today_event_count()

    # Most recent event (single item)
    recent = await store.get_recent_events(limit=1)
    last_event = recent[0] if recent else None

    # Lock status — get_lock_status() uses synchronous requests.get(), must run
    # in executor to avoid blocking the async event loop
    lock_status = None
    try:
        loop = asyncio.get_event_loop()
        lock_status = await loop.run_in_executor(None, switchbot.get_lock_status)
    except Exception:
        lock_status = None

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "today_count": today_count,
            "last_event": last_event,
            "lock_status": lock_status,
        },
    )


# ---------------------------------------------------------------------------
# Route: GET /dashboard/events — Event feed with filters + pagination
# (DASH-01, DASH-02, DASH-03, DASH-06)
# ---------------------------------------------------------------------------
@router.get("/events", response_class=HTMLResponse)
async def events_feed(
    request: Request,
    page: int = 1,
    limit: int = 20,
    date_range: str = "all",
    object_type: str = "all",
) -> HTMLResponse:
    """
    Paginated event feed with date range and object type filters.

    Query parameters:
        page        — Page number (1-based, default 1)
        limit       — Events per page (default 20)
        date_range  — One of: all, today, 7d, 30d (default all)
        object_type — One of: all, person, dog, cat, car, package (default all)

    Pagination uses the has_next heuristic: if len(events) == limit, a next
    page likely exists. This avoids a full COUNT(*) scan on every request.
    """
    store = request.app.state.store

    # Ensure page is at least 1 to prevent negative offsets
    if page < 1:
        page = 1

    offset = (page - 1) * limit

    events = await store.get_filtered_events(
        limit=limit,
        offset=offset,
        date_range=date_range,
        object_type=object_type,
    )

    # has_next heuristic: if we got a full page, there's likely another page
    has_next = len(events) == limit

    return templates.TemplateResponse(
        "events.html",
        {
            "request": request,
            "events": events,
            "page": page,
            "limit": limit,
            "date_range": date_range,
            "object_type": object_type,
            "has_next": has_next,
        },
    )


# ---------------------------------------------------------------------------
# Route: GET /dashboard/thumbnails/{filename} — Authenticated file serving
# (DASH-05)
# ---------------------------------------------------------------------------
@router.get("/thumbnails/{filename}")
async def serve_thumbnail(filename: str) -> FileResponse:
    """
    Serve a JPEG thumbnail with authentication enforced by router-level dependency.

    Security:
    - Path traversal prevention: filenames containing "/" or ".." are rejected
    - File existence check before serving
    - Auth enforced at router level (no additional check needed here)

    Returns:
        FileResponse with media_type="image/jpeg"

    Raises:
        HTTPException 400 if filename contains path traversal sequences
        HTTPException 404 if thumbnail file does not exist
    """
    # Block path traversal attempts
    if "/" in filename or ".." in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename",
        )

    path = Path(Config.THUMBNAILS_DIR) / filename

    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thumbnail not found",
        )

    return FileResponse(str(path), media_type="image/jpeg")
