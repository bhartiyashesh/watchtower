# Stack Research

**Domain:** Home surveillance analytics — adding object detection, event storage, web dashboard, and Telegram alerts to an existing Python smart lock system
**Researched:** 2026-02-24
**Confidence:** HIGH (core choices verified via PyPI official releases; performance benchmarks MEDIUM via multiple community sources)

---

## Context: What Already Exists (Do Not Re-Research)

The following are working and pinned in `requirements.txt`. Do not change these versions without testing:

| Component | Library | Version |
|-----------|---------|---------|
| Ring doorbell polling | ring-doorbell | 0.9.13 |
| Face detection/recognition | face-recognition + dlib | 1.3.0+ / 20.0.0 |
| Frame extraction | opencv-python | 4.8.0+ |
| SwitchBot API | requests | 2.31.0+ |
| Image processing | Pillow | 10.0.0+ |
| Async HTTP (Ring internal) | aiohttp | 3.13.3 |

**Runtime constraint:** Python 3.12 only. dlib is incompatible with 3.13. All new libraries must support 3.12.

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| ultralytics | 8.4.16 | Object detection (YOLO11n / YOLOv8n) | Official Ultralytics package — single install gives access to both YOLOv8 and the newer YOLO11 family. Version 8.4.16 released 2026-02-24. Python 3.8-3.12 compatible. yolov8n.pt / yolo11n.pt are the only models small enough for Raspberry Pi 4 CPU inference. |
| FastAPI | 0.133.0 | Web dashboard HTTP server + API | Released 2026-02-24. Async-first (shares the same event loop as aiosqlite), auto-generates OpenAPI docs, native WebSocket support for live event push, Jinja2 template rendering via Starlette. Outperforms Flask 5-8x on concurrent I/O. Critical for a 24/7 service that must also poll Ring. |
| uvicorn | 0.34.x | ASGI server for FastAPI | The canonical production server for FastAPI. Systemd + uvicorn is the standard deployment pattern for Raspberry Pi single-service Python apps — no Docker overhead needed. |
| aiosqlite | 0.22.1 | Async SQLite interface | Released 2025-12-23. Python 3.9+ (compatible with 3.12). Bridges SQLite to asyncio without blocking the event loop. v0.22.0 switched from polling to futures, making it significantly faster. Essential because FastAPI and the event loop cannot block on synchronous DB calls. |
| python-telegram-bot | 22.6 | Telegram bot for alerts | Released 2026-01-24. Python 3.10+ (compatible with 3.12). Async-native since v20. Only mandatory dependency is httpx. Supports `bot.send_photo()` for sending alert snapshots with captions. Most mature Telegram library — 22,000+ GitHub stars. |
| Jinja2 | 3.x | HTML template rendering | Installed automatically with `fastapi[standard]`. Renders the activity log, event detail pages, and heatmap views server-side. No JavaScript build toolchain needed — correct for a home server running on Raspberry Pi. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| aiofiles | 24.x | Async file I/O | Required for FastAPI to serve saved thumbnail images without blocking the event loop. Install alongside FastAPI for `StaticFiles` to perform well. |
| Pillow (already in stack) | 10.0.0+ | Thumbnail generation | Already a dependency. Use to crop and resize detection frames before saving as thumbnails (target 320x240 JPEG, ~15-25 KB per event). |
| httpx | 0.27+ | Async HTTP client | Installed as a dependency of python-telegram-bot. Can replace `requests` for SwitchBot calls if the codebase migrates to async. Not required to change immediately. |
| python-dotenv (already in stack) | 1.0.0+ | Config | Already in use. Add new env vars (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DB_PATH, DASHBOARD_PORT) to `.env` using the existing Config class pattern. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uvicorn --reload | Live reload during dashboard development | Use only in dev. In production systemd service, omit --reload and set --workers 1 (single-core RPi constraint). |
| SQLite CLI (`sqlite3`) | Schema inspection and manual queries | No extra install. Run `sqlite3 events.db .schema` to verify migrations applied correctly. |
| ultralytics CLI (`yolo`) | Model download and quick benchmark | Installed with ultralytics package. Run `yolo predict model=yolo11n.pt source=test_frame.jpg` to verify model works on the RPi before integrating. |

---

## Installation

```bash
# Object detection
pip install ultralytics==8.4.16

# Web dashboard + ASGI server
pip install "fastapi[standard]==0.133.0" uvicorn aiofiles

# Async SQLite
pip install aiosqlite==0.22.1

# Telegram alerts
pip install python-telegram-bot==22.6

# All together (add to requirements.txt)
ultralytics==8.4.16
fastapi[standard]==0.133.0
uvicorn==0.34.0
aiofiles==24.1.0
aiosqlite==0.22.1
python-telegram-bot==22.6
```

**Note on ultralytics model download:** The first run of `YOLO("yolo11n.pt")` downloads the model weights (~6 MB for nano) from ultralytics servers. Pre-download on development machine and copy `yolo11n.pt` to the Raspberry Pi to avoid download on first boot.

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| FastAPI | Flask | If the team strongly prefers synchronous code and has no async experience. Flask's WSGI model blocks on every DB call, which creates latency spikes on the dashboard when the background poller is also writing to SQLite. Not recommended for this architecture. |
| FastAPI | Flask | Flask is reasonable if the dashboard is purely read-only HTML with no WebSocket events. Unlikely given the requirement for real-time unlock/stranger alerts on the dashboard. |
| aiosqlite | SQLAlchemy async | If the schema grows complex and requires ORM-level relationship mapping. SQLAlchemy 2.0 supports async. Overkill for single-table event storage on a home server. |
| aiosqlite (raw SQL) | databases library | `databases` wraps aiosqlite with a query builder. Adds a dependency for marginal benefit. Use raw aiosqlite with hand-written SQL for this scale. |
| python-telegram-bot | pyTelegramBotAPI (telebot) | telebot has a simpler sync API. Use it if async integration proves difficult. However python-telegram-bot v22 has better type safety and is more actively maintained. |
| YOLO11n (nano) | YOLOv8n | Both come from the same `ultralytics` package install. YOLO11n has 22% fewer parameters than YOLOv8n with ~2% higher mAP. Switching is one string change: `YOLO("yolo11n.pt")`. Prefer YOLO11n as it is the current default model. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| YOLOv8m / YOLOv8l / YOLO11s+ | Medium and large models exceed Raspberry Pi 4's 4 GB RAM and produce 1-3 FPS on CPU — unusable for batch processing 30-90s event clips | yolo11n.pt or yolov8n.pt (nano only) |
| PyTorch format (.pt) for production inference on RPi | PyTorch inference on ARM CPU is 2-3x slower than NCNN-exported models | Export to NCNN format with `model.export(format="ncnn")` after verifying nano model works. Use NCNN for production, .pt for development. |
| Flask | Synchronous WSGI blocks the thread during SQLite writes. A poller + web server sharing one Python process will cause the dashboard to stall during Ring polling | FastAPI + uvicorn (ASGI, non-blocking) |
| Django | Full ORM + admin + migrations machinery. 4x the memory footprint of FastAPI. No benefit for a single-user home dashboard | FastAPI with Jinja2 templates |
| telebot (pyTelegramBotAPI) synchronous mode | Calling `bot.send_photo()` synchronously inside an async event loop blocks the loop | python-telegram-bot 22.x with `await bot.send_photo()` |
| PostgreSQL / MySQL | Requires a running database server process. Adds 50-200 MB RAM overhead. Overkill for single-home event volume (estimated 10-100 events/day) | SQLite with WAL mode enabled |
| Celery / task queues | YOLO inference is fast enough to run inline after event download (~0.5-2s per frame on RPi CPU). A task queue adds a Redis/RabbitMQ dependency with no benefit at this scale | Run YOLO inference synchronously in the event processing coroutine |
| OpenCV VideoCapture for streaming | The system receives pre-recorded MP4 clips from Ring, not live streams. OpenCV frame extraction on saved files is already working | Keep existing cv2 frame extraction from MP4 |

---

## Stack Patterns by Variant

**Development (macOS):**
- Use PyTorch format `.pt` models directly — no NCNN export needed
- Run uvicorn with `--reload` flag
- SQLite WAL mode on, single connection

**Production (Raspberry Pi 4):**
- Export YOLO model to NCNN: `model.export(format="ncnn")` then load `YOLO("yolo11n_ncnn_model")`
- Run uvicorn with `--host 0.0.0.0 --port 8080 --workers 1` via systemd service
- SQLite: enable `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` at connection time
- Set uvicorn `--no-access-log` to reduce SD card writes

**If Raspberry Pi 5 replaces Pi 4:**
- Can step up to yolo11s (small) model — ~25% more accurate, still acceptable CPU speed
- Can run 2 uvicorn workers without memory pressure

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| ultralytics==8.4.16 | Python 3.8-3.12, PyTorch 1.8+ | Do NOT use Python 3.13 — dlib blocks upgrade. Confirmed Python 3.12 support. |
| fastapi==0.133.0 | Python 3.10+, Pydantic v2, Starlette 0.45.x | Python 3.12 is in supported range. Installs Starlette and Pydantic v2 automatically. |
| aiosqlite==0.22.1 | Python 3.9+, standard sqlite3 module | Python 3.12 supported. No binary extension — pure Python bridge. |
| python-telegram-bot==22.6 | Python 3.10+, httpx 0.27-0.28 | Python 3.12 supported. httpx will already be installed by FastAPI standard extras — ensure version ranges don't conflict. |
| ultralytics + opencv-python | Both require numpy | ultralytics brings its own numpy requirement. Existing opencv-python 4.8.0 is compatible — verify no numpy version conflict after install with `pip check`. |

---

## SQLite Schema Pattern

```sql
-- Enable WAL at connection time (run once per connection)
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- Core events table
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT UNIQUE NOT NULL,         -- Ring event ID
    camera_id   TEXT NOT NULL,                -- Ring doorbell device ID
    occurred_at DATETIME NOT NULL,            -- Ring event timestamp (UTC)
    processed_at DATETIME,                    -- When our pipeline ran
    face_name   TEXT,                         -- NULL = stranger, 'unknown' = no face detected
    face_confidence REAL,
    yolo_detections TEXT,                     -- JSON: [{"class": "person", "confidence": 0.91}, ...]
    thumbnail_path TEXT,                      -- Relative path to saved JPEG thumbnail
    door_action TEXT,                         -- 'unlocked', 'locked', 'none'
    alert_sent  INTEGER DEFAULT 0             -- 1 if Telegram alert was dispatched
);

-- Index for dashboard queries
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_face_name ON events(face_name);
CREATE INDEX IF NOT EXISTS idx_events_camera_id ON events(camera_id);
```

This schema is intentionally flat. No normalization needed at single-home event volumes. YOLO detections stored as JSON text — fast enough to query by `LIKE '%"person"%'` and avoids a join table.

---

## Sources

- [ultralytics PyPI](https://pypi.org/project/ultralytics/) — version 8.4.16, Python 3.8-3.12 support (HIGH confidence, official)
- [Ultralytics Raspberry Pi guide](https://docs.ultralytics.com/guides/raspberry-pi/) — NCNN recommendation, nano model sizing (HIGH confidence, official docs)
- [FastAPI PyPI](https://pypi.org/project/fastapi/) — version 0.133.0, Python 3.10+ (HIGH confidence, official)
- [python-telegram-bot PyPI](https://pypi.org/project/python-telegram-bot/) — version 22.6, Python 3.10+, httpx dependency (HIGH confidence, official)
- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) — version 0.22.1, Python 3.9+ (HIGH confidence, official)
- [SQLite WAL mode docs](https://sqlite.org/wal.html) — concurrent read/write behavior (HIGH confidence, official SQLite docs)
- [FastAPI vs Flask 2025 comparison — Strapi blog](https://strapi.io/blog/fastapi-vs-flask-python-framework-comparison) — performance benchmark rationale (MEDIUM confidence, verified against FastAPI docs)
- [YOLO11 vs YOLOv8 — Ultralytics docs](https://docs.ultralytics.com/compare/yolo11-vs-yolov8/) — 22% fewer parameters, 2% mAP gain (HIGH confidence, official)
- [FastAPI static files — official tutorial](https://fastapi.tiangolo.com/tutorial/static-files/) — StaticFiles mount pattern for thumbnails (HIGH confidence, official)

---

*Stack research for: Smart Lock Analytics Platform — object detection, storage, dashboard, and alerts milestone*
*Researched: 2026-02-24*
