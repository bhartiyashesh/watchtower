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
import logging
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from PIL import Image, ImageDraw, ImageFont

from config import Config

logger = logging.getLogger(__name__)

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


def _human_datetime(value: str) -> str:
    """Convert ISO timestamp like '2026-02-26T20:59:49.558588' to 'Feb 26, 2026 8:59 PM'."""
    from datetime import datetime
    if not value:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%b %d, %Y %-I:%M %p")
        except ValueError:
            continue
    return value


templates.env.filters["humandate"] = _human_datetime


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


# ---------------------------------------------------------------------------
# Route: GET /dashboard/analytics — 3D Analytics Page
# ---------------------------------------------------------------------------
@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request) -> HTMLResponse:
    """Render the analytics dashboard with Three.js + D3 visualizations."""
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "config": Config,
        },
    )


# ---------------------------------------------------------------------------
# JSON API endpoints for analytics charts (consumed by client-side JS)
# ---------------------------------------------------------------------------

@router.get("/api/stats")
async def api_stats(request: Request) -> JSONResponse:
    store = request.app.state.store
    data = await store.get_analytics_stats()
    return JSONResponse(data)


@router.get("/api/hourly-heatmap")
async def api_hourly_heatmap(request: Request, days: int = Query(30, ge=1, le=365)) -> JSONResponse:
    store = request.app.state.store
    data = await store.get_hourly_heatmap(days=days)
    return JSONResponse(data)


@router.get("/api/detection-breakdown")
async def api_detection_breakdown(request: Request, days: int = Query(30, ge=1, le=365)) -> JSONResponse:
    store = request.app.state.store
    data = await store.get_detection_breakdown(days=days)
    return JSONResponse(data)


@router.get("/api/daily-timeline")
async def api_daily_timeline(request: Request, days: int = Query(30, ge=1, le=365)) -> JSONResponse:
    store = request.app.state.store
    data = await store.get_daily_timeline(days=days)
    return JSONResponse(data)


@router.get("/api/peak-hours")
async def api_peak_hours(request: Request, days: int = Query(30, ge=1, le=365)) -> JSONResponse:
    store = request.app.state.store
    data = await store.get_peak_hours(days=days)
    return JSONResponse(data)


@router.get("/api/recent-orb")
async def api_recent_orb(request: Request, limit: int = Query(50, ge=1, le=200)) -> JSONResponse:
    store = request.app.state.store
    data = await store.get_recent_events_for_orb(limit=limit)
    return JSONResponse(data)


@router.get("/api/battery")
async def api_battery(request: Request) -> JSONResponse:
    """Return Ring doorbell battery percentage."""
    ring = request.app.state.ring
    level = ring.get_battery_level()
    return JSONResponse({"battery": level})


@router.get("/api/lock-status")
async def api_lock_status(request: Request) -> JSONResponse:
    """Return current lock state."""
    switchbot = request.app.state.switchbot
    loop = asyncio.get_event_loop()
    status = await loop.run_in_executor(None, switchbot.get_lock_status)
    state = "unknown"
    if status and isinstance(status, dict):
        state = status.get("lockState", "unknown")
    return JSONResponse({"state": state})


@router.post("/api/lock")
async def api_lock(request: Request) -> JSONResponse:
    """Send lock command to SwitchBot."""
    switchbot = request.app.state.switchbot
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, switchbot.lock)
    if not ok:
        raise HTTPException(status_code=500, detail="Lock command failed")
    return JSONResponse({"status": "locked"})


@router.post("/api/unlock")
async def api_unlock(request: Request) -> JSONResponse:
    """Send unlock command to SwitchBot."""
    switchbot = request.app.state.switchbot
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, switchbot.unlock)
    if not ok:
        raise HTTPException(status_code=500, detail="Unlock command failed")
    return JSONResponse({"status": "unlocked"})


# ---------------------------------------------------------------------------
# Timelapse feature
# ---------------------------------------------------------------------------

@router.get("/timelapse", response_class=HTMLResponse)
async def timelapse_page(request: Request) -> HTMLResponse:
    """Render the timelapse generator page with date picker and video player."""
    return templates.TemplateResponse("timelapse.html", {"request": request})


@router.get("/api/timelapse-info")
async def api_timelapse_info(
    request: Request,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> JSONResponse:
    """Return thumbnail count for a given date (used by frontend preview)."""
    store = request.app.state.store
    events = await store.get_events_for_timelapse(date)
    return JSONResponse({"date": date, "count": len(events)})


def _burn_overlay(src_path: str, dst_path: str, timestamp: str, person_name: str | None) -> None:
    """Burn timestamp + person name overlay onto a thumbnail copy."""
    img = Image.open(src_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size

    bar_height = 40
    # Semi-transparent dark bar at bottom
    draw.rectangle([(0, h - bar_height), (w, h)], fill=(0, 0, 0, 180))

    font = ImageFont.load_default()
    label = person_name if person_name else "Unknown"

    # Timestamp left-aligned, person name right-aligned
    draw.text((8, h - bar_height + 12), timestamp, fill="white", font=font)
    # Right-align the person label
    bbox = font.getbbox(label)
    text_w = bbox[2] - bbox[0]
    draw.text((w - text_w - 8, h - bar_height + 12), label, fill="white", font=font)

    img.save(dst_path, "JPEG", quality=90)


def _cleanup_tmpdir(tmpdir: str) -> None:
    """Remove temporary directory used for timelapse generation."""
    shutil.rmtree(tmpdir, ignore_errors=True)


@router.get("/api/timelapse")
async def api_timelapse(
    request: Request,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    fps: int = Query(2, ge=1, le=5),
) -> FileResponse:
    """Generate a timelapse MP4 from a day's thumbnails and stream it back."""
    store = request.app.state.store
    events = await store.get_events_for_timelapse(date)

    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No events with thumbnails found for this date",
        )

    tmpdir = tempfile.mkdtemp(prefix="timelapse_")

    try:
        # Burn overlays onto copies of each thumbnail
        for i, event in enumerate(events):
            src = event["thumbnail_path"]
            if not Path(src).exists():
                continue
            dst = Path(tmpdir) / f"{i + 1:04d}.jpg"
            _burn_overlay(src, str(dst), event["recorded_at"], event["person_name"])

        # Check that at least one frame was produced
        frame_count = len(list(Path(tmpdir).glob("*.jpg")))
        if frame_count == 0:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No valid thumbnail files found on disk for this date",
            )

        # Run FFmpeg in executor thread
        output_path = Path(tmpdir) / "timelapse.mp4"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_ffmpeg, tmpdir, str(output_path), fps)

        if not output_path.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="FFmpeg failed to produce output",
            )

        return FileResponse(
            str(output_path),
            media_type="video/mp4",
            filename=f"timelapse-{date}.mp4",
            headers={"Content-Disposition": f'attachment; filename="timelapse-{date}.mp4"'},
            background=BackgroundTask(_cleanup_tmpdir, tmpdir),
        )

    except HTTPException:
        raise
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        logger.exception("Timelapse generation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Timelapse generation failed",
        )


def _find_ffmpeg() -> str:
    """Locate ffmpeg binary, checking common Homebrew paths when PATH is limited."""
    for candidate in ("ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if shutil.which(candidate):
            return candidate
    raise FileNotFoundError("ffmpeg not found on this system")


def _run_ffmpeg(tmpdir: str, output_path: str, fps: int) -> None:
    """Run FFmpeg to stitch numbered frames into an MP4."""
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", f"{tmpdir}/%04d.jpg",
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("FFmpeg stderr: %s", result.stderr)
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}")


# ---------------------------------------------------------------------------
# People management — face enrollment & auto-unlock
# ---------------------------------------------------------------------------

@router.get("/people", response_class=HTMLResponse)
async def people_page(request: Request) -> HTMLResponse:
    """Render the People management page."""
    store = request.app.state.store
    # Backfill persons table from any faces already on disk
    await store.sync_persons_from_disk(Config.KNOWN_FACES_DIR)
    persons = await store.get_persons(Config.KNOWN_FACES_DIR)
    return templates.TemplateResponse("people.html", {"request": request, "persons": persons})


@router.get("/api/people")
async def api_list_people(request: Request) -> JSONResponse:
    """List all persons as JSON."""
    store = request.app.state.store
    await store.sync_persons_from_disk(Config.KNOWN_FACES_DIR)
    persons = await store.get_persons(Config.KNOWN_FACES_DIR)
    return JSONResponse(persons)


@router.post("/api/people")
async def api_add_person(
    request: Request,
    name: str = Form(...),
    photo: UploadFile = File(...),
) -> JSONResponse:
    """Upload a face photo and create/add-to a person."""
    store = request.app.state.store
    recognizer = request.app.state.recognizer

    # Sanitize name: lowercase, replace spaces with underscores, strip non-alphanum
    clean_name = name.strip().lower().replace(" ", "_")
    clean_name = "".join(c for c in clean_name if c.isalnum() or c == "_")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Invalid name")

    # Read uploaded file
    image_bytes = await photo.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Determine extension from uploaded filename
    ext = Path(photo.filename).suffix.lower() if photo.filename else ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".bmp"}:
        ext = ".jpg"

    # Enroll the face (validates face presence, saves to disk, reloads recognizer)
    loop = asyncio.get_event_loop()
    try:
        filename = await loop.run_in_executor(
            None, recognizer.enroll, clean_name, image_bytes, ext
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Upsert person in DB
    display_name = name.strip().title()
    await store.add_person(clean_name, display_name)

    # Return updated person info
    persons = await store.get_persons(Config.KNOWN_FACES_DIR)
    person = next((p for p in persons if p["name"] == clean_name), None)

    return JSONResponse(
        {"status": "ok", "filename": filename, "person": person},
        status_code=201,
    )


@router.delete("/api/people/{name}")
async def api_delete_person(request: Request, name: str) -> JSONResponse:
    """Delete a person and all their face images."""
    store = request.app.state.store
    recognizer = request.app.state.recognizer

    deleted = await store.delete_person(name, Config.KNOWN_FACES_DIR)
    if not deleted:
        raise HTTPException(status_code=404, detail="Person not found")

    # Reload recognizer so deleted faces are no longer matched
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, recognizer.reload)

    return JSONResponse({"status": "ok"})


@router.post("/api/people/{name}/auto-unlock")
async def api_toggle_auto_unlock(request: Request, name: str) -> JSONResponse:
    """Toggle a person's auto-unlock setting."""
    store = request.app.state.store

    body = await request.json()
    enabled = body.get("enabled", True)

    updated = await store.set_person_auto_unlock(name, enabled)
    if not updated:
        raise HTTPException(status_code=404, detail="Person not found")

    return JSONResponse({"status": "ok", "auto_unlock": enabled})


@router.get("/people/photos/{filename}")
async def serve_face_photo(filename: str) -> FileResponse:
    """Serve face photos from the known_faces directory."""
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = Config.KNOWN_FACES_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found")

    return FileResponse(str(path), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Static assets (background image, etc.)
# ---------------------------------------------------------------------------

_ALLOWED_ASSETS = {
    "background.png": "transparent_drawing.png",
}


@router.get("/assets/{filename}")
async def serve_asset(filename: str) -> FileResponse:
    """Serve whitelisted static assets from the project root."""
    if filename not in _ALLOWED_ASSETS:
        raise HTTPException(status_code=404, detail="Asset not found")

    path = Path(_ALLOWED_ASSETS[filename])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")

    media = "image/png" if filename.endswith(".png") else "application/octet-stream"
    return FileResponse(str(path), media_type=media)
