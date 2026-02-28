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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from PIL import Image, ImageDraw, ImageFont

from config import Config
from _paths import BUNDLE_DIR
from setup.env_writer import read_env, write_env

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

templates = Jinja2Templates(directory=str(BUNDLE_DIR / "dashboard" / "templates"))

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

    blink = getattr(request.app.state, "blink", None)
    blink_configured = blink is not None
    blink_needs_2fa = blink.needs_2fa if blink else False

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "today_count": today_count,
            "last_event": last_event,
            "lock_status": lock_status,
            "blink_configured": blink_configured,
            "blink_needs_2fa": blink_needs_2fa,
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


@router.get("/api/blink-snapshot")
async def api_blink_snapshot(request: Request):
    """Return a JPEG snapshot from the Blink camera."""
    from fastapi.responses import Response

    blink = getattr(request.app.state, "blink", None)
    if blink is None:
        raise HTTPException(status_code=404, detail="Blink camera not configured")

    if blink.needs_2fa:
        raise HTTPException(status_code=403, detail="Blink 2FA verification required")

    image_bytes = await blink.get_snapshot()

    if image_bytes is None:
        raise HTTPException(status_code=502, detail="Failed to fetch snapshot from Blink")

    return Response(content=image_bytes, media_type="image/jpeg")


@router.get("/api/blink-analyze")
async def api_blink_analyze(request: Request) -> JSONResponse:
    """Fetch a Blink snapshot, run YOLO detection, and return annotated results.

    Returns JSON with:
      - image: base64-encoded JPEG with bounding boxes drawn
      - detections: list of {label, confidence}
      - summary: human-readable text like "2 persons, 1 dog"
    """
    import base64
    import io

    blink = getattr(request.app.state, "blink", None)
    if blink is None:
        raise HTTPException(status_code=404, detail="Blink camera not configured")
    if blink.needs_2fa:
        raise HTTPException(status_code=403, detail="Blink 2FA verification required")

    detector = getattr(request.app.state, "detector", None)
    if detector is None:
        raise HTTPException(status_code=503, detail="Object detector not available")

    image_bytes = await blink.get_snapshot()
    if image_bytes is None:
        raise HTTPException(status_code=502, detail="Failed to fetch snapshot from Blink")

    # Run YOLO detection
    detections = await detector.detect(image_bytes)

    # Draw bounding boxes on image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    LABEL_COLORS = {
        "person": (68, 204, 136),
        "car": (100, 149, 237),
        "cat": (255, 165, 0),
        "dog": (255, 136, 68),
        "package": (186, 85, 211),
    }

    for det in detections:
        color = LABEL_COLORS.get(det.label, (255, 255, 255))
        box = (det.bbox_x1, det.bbox_y1, det.bbox_x2, det.bbox_y2)
        draw.rectangle(box, outline=color, width=3)

        label_text = f"{det.label} {det.confidence:.0%}"
        bbox = font.getbbox(label_text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        # Label background
        draw.rectangle(
            [det.bbox_x1, det.bbox_y1 - text_h - 6, det.bbox_x1 + text_w + 8, det.bbox_y1],
            fill=color,
        )
        draw.text((det.bbox_x1 + 4, det.bbox_y1 - text_h - 4), label_text, fill="white", font=font)

    # Encode annotated image to base64
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64_image = base64.b64encode(buf.getvalue()).decode("ascii")

    # Build summary text
    from collections import Counter
    counts = Counter(d.label for d in detections)
    if counts:
        parts = []
        for label, count in counts.most_common():
            parts.append(f"{count} {label}" if count == 1 else f"{count} {label}s")
        summary = "Detected: " + ", ".join(parts)
    else:
        summary = "No objects detected"

    return JSONResponse({
        "image": b64_image,
        "detections": [
            {"label": d.label, "confidence": round(d.confidence, 3)}
            for d in detections
        ],
        "summary": summary,
    })


@router.post("/api/blink-2fa")
async def api_blink_2fa(request: Request) -> JSONResponse:
    """Submit a Blink 2FA verification code."""
    blink = getattr(request.app.state, "blink", None)
    if blink is None:
        raise HTTPException(status_code=404, detail="Blink camera not configured")

    if not blink.needs_2fa:
        return JSONResponse({"status": "ok", "message": "Already verified"})

    body = await request.json()
    code = body.get("code", "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Verification code is required")

    success = await blink.submit_2fa(code)
    if not success:
        raise HTTPException(status_code=401, detail="Invalid verification code")

    return JSONResponse({"status": "ok", "message": "Blink camera verified"})


@router.get("/api/blink-arm-status")
async def api_blink_arm_status(request: Request) -> JSONResponse:
    """Return current Blink camera arm state and motion detection status."""
    blink = getattr(request.app.state, "blink", None)
    if blink is None or blink.needs_2fa or blink._camera is None:
        raise HTTPException(status_code=404, detail="Blink camera not available")

    armed = blink._camera.arm
    # blinkpy returns None before first refresh — treat as unknown/false
    if armed is None:
        armed = False

    return JSONResponse({
        "armed": bool(armed),
        "motion_detected": bool(blink._camera.motion_detected or False),
    })


@router.post("/api/blink-arm")
async def api_blink_arm(request: Request) -> JSONResponse:
    """Arm or disarm the Blink camera for motion detection."""
    blink = getattr(request.app.state, "blink", None)
    if blink is None or blink.needs_2fa or blink._camera is None:
        raise HTTPException(status_code=404, detail="Blink camera not available")

    body = await request.json()
    armed = body.get("armed", True)

    await blink._camera.async_arm(armed)
    return JSONResponse({"armed": armed})


@router.get("/api/blink-live")
async def api_blink_live(request: Request):
    """Stream live MJPEG video from the Blink camera.

    Starts the BlinkLiveStream (IMMIS → local TCP MPEG-TS), spawns FFmpeg
    to transcode to MJPEG, and pipes the output as a multipart stream that
    browsers render natively in an <img> tag.

    Auto-reconnects when the Blink stream session ends (sessions are
    limited to ~30-90s by the Blink cloud).
    """
    blink = getattr(request.app.state, "blink", None)
    if blink is None:
        raise HTTPException(status_code=404, detail="Blink camera not configured")
    if blink.needs_2fa:
        raise HTTPException(status_code=403, detail="Blink 2FA verification required")

    ffmpeg_bin = _find_ffmpeg()

    async def _start_ffmpeg_session():
        """Start a Blink livestream + FFmpeg process.  Returns (proc, stderr_task)."""
        tcp_url = await blink.start_livestream()
        # Brief buffer so FFmpeg has TS packets waiting when it connects
        await asyncio.sleep(0.5)

        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            # Input: known MPEG-TS, minimal probe for instant start
            "-f", "mpegts",
            "-analyzeduration", "500000",     # 0.5s — just enough for codec detection
            "-probesize", "200000",           # 200 KB probe window
            "-rw_timeout", "15000000",        # 15s TCP timeout
            "-fflags", "+nobuffer+discardcorrupt",
            "-flags", "low_delay",
            "-i", tcp_url,
            # Output: MJPEG to stdout
            "-f", "mjpeg",
            "-q:v", "5",
            "-an",
            "-r", "15",
            "-vf", "scale=1280:-2",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Log FFmpeg stderr in background so errors are visible
        async def _drain_stderr():
            while proc.stderr and not proc.stderr.at_eof():
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                if text:
                    logger.info("ffmpeg(live): %s", text)
        stderr_task = asyncio.create_task(_drain_stderr())
        return proc, stderr_task

    # Start the initial session
    try:
        proc, stderr_task = await _start_ffmpeg_session()
    except Exception:
        logger.exception("Failed to start Blink livestream")
        raise HTTPException(status_code=502, detail="Failed to start livestream")

    request.app.state._blink_ffmpeg = proc

    async def mjpeg_generator():
        """Read JPEG frames from FFmpeg, auto-reconnecting when the Blink session ends."""
        nonlocal proc, stderr_task
        JPEG_START = b"\xff\xd8"
        JPEG_END = b"\xff\xd9"
        MAX_RECONNECTS = 20  # ~10-30 minutes of viewing depending on session length
        reconnects = 0
        stop_requested = False

        try:
            while reconnects <= MAX_RECONNECTS:
                buf = b""
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        start = buf.find(JPEG_START)
                        if start == -1:
                            buf = b""
                            break
                        end = buf.find(JPEG_END, start + 2)
                        if end == -1:
                            buf = buf[start:]
                            break
                        frame = buf[start : end + 2]
                        buf = buf[end + 2 :]
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                            b"\r\n" + frame + b"\r\n"
                        )

                # FFmpeg exited — clean up this session
                await proc.wait()
                await blink.stop_livestream()

                # Check if we should reconnect
                if stop_requested:
                    break

                reconnects += 1
                logger.info("Blink stream session ended, reconnecting (%d/%d)", reconnects, MAX_RECONNECTS)

                try:
                    proc, stderr_task = await _start_ffmpeg_session()
                    request.app.state._blink_ffmpeg = proc
                except Exception:
                    logger.warning("Failed to reconnect Blink livestream")
                    break

        except (asyncio.CancelledError, GeneratorExit):
            stop_requested = True
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            await blink.stop_livestream()
            request.app.state._blink_ffmpeg = None

    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.api_route("/api/blink-live-stop", methods=["GET", "POST"])
async def api_blink_live_stop(request: Request):
    """Explicitly stop the Blink livestream and FFmpeg process."""
    blink = getattr(request.app.state, "blink", None)
    proc = getattr(request.app.state, "_blink_ffmpeg", None)

    if proc and proc.returncode is None:
        proc.kill()
        await proc.wait()
    request.app.state._blink_ffmpeg = None

    if blink:
        await blink.stop_livestream()

    return JSONResponse({"status": "ok", "message": "Livestream stopped"})


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

# ---------------------------------------------------------------------------
# Settings page — view/update device & general config
# ---------------------------------------------------------------------------

# Whitelist of .env keys allowed per settings section (prevents arbitrary writes)
_SETTINGS_WHITELIST: dict[str, list[str]] = {
    "ring": ["RING_USERNAME", "RING_PASSWORD"],
    "switchbot": ["SWITCHBOT_TOKEN", "SWITCHBOT_SECRET", "SWITCHBOT_DEVICE_ID"],
    "blink": ["BLINK_USERNAME", "BLINK_PASSWORD", "BLINK_CAMERA_NAME"],
    "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
    "general": [
        "POLL_INTERVAL", "UNLOCK_COOLDOWN", "FACE_MATCH_TOLERANCE",
        "DASHBOARD_USERNAME", "DASHBOARD_PASSWORD",
        "ANALYTICS_LATITUDE", "ANALYTICS_LONGITUDE",
    ],
}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Render the Settings / Devices page."""
    env = read_env()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "config": Config, "env": env},
    )


@router.post("/api/settings")
async def api_save_settings(request: Request) -> JSONResponse:
    """Save settings for a given section to .env and reload Config."""
    body = await request.json()
    section = body.get("section", "")
    values = body.get("values", {})

    if section not in _SETTINGS_WHITELIST:
        raise HTTPException(status_code=400, detail=f"Unknown section: {section}")

    allowed_keys = _SETTINGS_WHITELIST[section]
    updates = {}
    for key, value in values.items():
        if key not in allowed_keys:
            raise HTTPException(status_code=400, detail=f"Key not allowed: {key}")
        updates[key] = str(value)

    if not updates:
        return JSONResponse({"ok": True, "restart_needed": False, "message": "Nothing to update"})

    write_env(updates=updates)
    Config.reload()

    restart_needed = section in ("ring", "switchbot", "blink", "telegram")
    message = "Settings saved."
    if restart_needed:
        message += " Restart WatchTower for device changes to take full effect."

    return JSONResponse({"ok": True, "restart_needed": restart_needed, "message": message})


# ---------------------------------------------------------------------------
# Blink multi-step setup: login → 2FA → camera list → save
# ---------------------------------------------------------------------------

@router.post("/api/settings/blink-login")
async def api_settings_blink_login(request: Request) -> JSONResponse:
    """Step 1: Test Blink credentials. If 2FA is needed, store the instance."""
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()

    if not username or not password:
        return JSONResponse({"ok": False, "error": "Email and password are required."})

    try:
        from blink_client import BlinkClient, BLINK_CREDS_CACHE

        blink = BlinkClient(username, password)
        await blink.authenticate()

        if blink.needs_2fa:
            request.app.state.settings_blink = blink
            return JSONResponse({"ok": True, "needs_2fa": True, "cameras": []})

        cameras = list(blink.blink.cameras.keys()) if blink.blink and blink.blink.cameras else []

        if not cameras and BLINK_CREDS_CACHE.exists():
            # Stale cached credentials let blinkpy skip 2FA but the session
            # is actually dead.  Delete the cache and retry fresh — this will
            # trigger the real 2FA flow.
            BLINK_CREDS_CACHE.unlink(missing_ok=True)
            await blink.stop()

            blink = BlinkClient(username, password)
            await blink.authenticate()

            if blink.needs_2fa:
                request.app.state.settings_blink = blink
                return JSONResponse({"ok": True, "needs_2fa": True, "cameras": []})

            cameras = list(blink.blink.cameras.keys()) if blink.blink and blink.blink.cameras else []

        request.app.state.settings_blink = blink
        return JSONResponse({"ok": True, "needs_2fa": False, "cameras": cameras})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Blink login failed: {e}"})


@router.post("/api/settings/blink-2fa")
async def api_settings_blink_2fa(request: Request) -> JSONResponse:
    """Step 2: Submit 2FA code and return camera list on success."""
    body = await request.json()
    code = body.get("code", "").strip()
    if not code:
        return JSONResponse({"ok": False, "error": "Verification code is required."})

    blink = getattr(request.app.state, "settings_blink", None)
    if blink is None:
        return JSONResponse({"ok": False, "error": "No pending Blink session. Log in first."})

    success = await blink.submit_2fa(code)
    if not success:
        return JSONResponse({"ok": False, "error": "Invalid verification code."})

    cameras = list(blink.blink.cameras.keys()) if blink.blink and blink.blink.cameras else []
    return JSONResponse({"ok": True, "cameras": cameras})


@router.post("/api/settings/blink-save")
async def api_settings_blink_save(request: Request) -> JSONResponse:
    """Step 3: Save Blink credentials + selected camera to .env."""
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    camera_name = body.get("camera_name", "").strip()

    if not username or not password:
        return JSONResponse({"ok": False, "error": "Email and password are required."})

    updates = {
        "BLINK_USERNAME": username,
        "BLINK_PASSWORD": password,
        "BLINK_CAMERA_NAME": camera_name,
    }
    write_env(updates=updates)
    Config.reload()

    # Promote the authenticated settings session to the live operational slot
    # so the dashboard snapshot works immediately without a full restart.
    settings_blink = getattr(request.app.state, "settings_blink", None)
    if settings_blink and settings_blink.blink and not settings_blink.needs_2fa:
        # Set the selected camera name so _find_camera picks the right one
        settings_blink.camera_name = camera_name
        settings_blink._camera = settings_blink._find_camera()

        old_blink = getattr(request.app.state, "blink", None)
        if old_blink:
            await old_blink.stop()
        request.app.state.blink = settings_blink
        request.app.state.settings_blink = None

        return JSONResponse({
            "ok": True,
            "restart_needed": False,
            "message": "Blink camera connected and ready.",
        })

    # Fallback if no live session available
    if settings_blink:
        await settings_blink.stop()
        request.app.state.settings_blink = None

    return JSONResponse({
        "ok": True,
        "restart_needed": True,
        "message": "Blink settings saved. Restart WatchTower for changes to take effect.",
    })


# ---------------------------------------------------------------------------
# Static assets (background image, etc.)
# ---------------------------------------------------------------------------

_ALLOWED_ASSETS = {
    "background.png": "transparent_drawing.png",
}


@router.get("/assets/{filename}")
async def serve_asset(filename: str) -> FileResponse:
    """Serve whitelisted static assets from the bundle directory."""
    if filename not in _ALLOWED_ASSETS:
        raise HTTPException(status_code=404, detail="Asset not found")

    path = BUNDLE_DIR / _ALLOWED_ASSETS[filename]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")

    media = "image/png" if filename.endswith(".png") else "application/octet-stream"
    return FileResponse(str(path), media_type=media)
