# Architecture Research

**Domain:** Python home surveillance analytics platform (async event pipeline + web dashboard)
**Researched:** 2026-02-24
**Confidence:** HIGH (core patterns verified against official FastAPI docs, python-telegram-bot docs, SQLite official docs, ultralytics docs)

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Process Entry Point                          │
│                            main.py                                   │
│   Launches FastAPI app (uvicorn) + Ring polling loop as asyncio      │
│   tasks within a single event loop via lifespan context manager      │
└──────────────────┬─────────────────────────────────────┬────────────┘
                   │                                     │
       ┌───────────▼───────────┐             ┌───────────▼───────────┐
       │   Async Polling Loop  │             │   FastAPI Web Server  │
       │   (asyncio.Task)      │             │   (uvicorn ASGI)      │
       │                       │             │                       │
       │  Ring poll → frame    │             │  GET /events          │
       │  YOLO (executor)      │             │  GET /heatmap         │
       │  face recognize       │             │  GET /thumbnails      │
       │  SwitchBot unlock     │             │  GET /stats           │
       │  DB write             │             │  WebSocket /live      │
       └──────────┬────────────┘             └──────────┬────────────┘
                  │                                     │
                  └──────────────┬──────────────────────┘
                                 │
              ┌──────────────────▼──────────────────┐
              │         Shared Services Layer        │
              │                                      │
              │  ┌────────────┐  ┌────────────────┐  │
              │  │ EventStore │  │ TelegramAlerter│  │
              │  │ (aiosqlite)│  │ (Bot.send_*)   │  │
              │  └─────┬──────┘  └────────────────┘  │
              │        │                              │
              │  ┌─────▼──────┐  ┌────────────────┐  │
              │  │ SQLite DB  │  │ thumbnails/     │  │
              │  │ events.db  │  │ (filesystem)    │  │
              │  └────────────┘  └────────────────┘  │
              └──────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Communicates With |
|-----------|----------------|-------------------|
| `main.py` | Process entry, launch uvicorn + polling loop as concurrent asyncio tasks | All |
| `ring_client.py` | Ring auth, event polling, video download, frame extraction | main.py |
| `object_detector.py` | YOLOv8 inference (CPU, run via executor) returning Detection objects | main.py |
| `face_recognizer.py` | dlib face encoding and matching; returns person name or None | main.py |
| `switchbot_client.py` | HMAC-signed SwitchBot Cloud API calls | main.py |
| `event_store.py` | aiosqlite write/query wrapper for events, detections, persons | polling loop + dashboard routes |
| `telegram_alerter.py` | python-telegram-bot Bot.send_message/send_photo calls | polling loop |
| `dashboard/` | FastAPI router with /events, /heatmap, /thumbnails, /stats routes | event_store.py |
| `config.py` | Environment parsing, validation, all feature flags | All |
| `enroll_face.py` | Standalone CLI for face enrollment | none |
| `discover_devices.py` | Standalone CLI for SwitchBot device discovery | none |

## Recommended Project Structure

```
smart-lock-system/
├── main.py                      # Entry: starts uvicorn + polling loop together
├── config.py                    # Env parsing, validation, all config
│
├── ring_client.py               # Ring SDK wrapper (existing)
├── face_recognizer.py           # dlib face encoding/matching (existing)
├── switchbot_client.py          # SwitchBot API client (existing)
│
├── object_detector.py           # NEW: YOLOv8 wrapper (sync model, async interface)
├── event_store.py               # NEW: aiosqlite write/query service
├── telegram_alerter.py          # NEW: Telegram Bot wrapper (async send)
│
├── dashboard/                   # NEW: FastAPI sub-application
│   ├── __init__.py
│   ├── app.py                   # FastAPI app factory, lifespan, router mount
│   ├── routes/
│   │   ├── events.py            # GET /events, GET /events/{id}
│   │   ├── heatmap.py           # GET /heatmap (aggregated time-of-day data)
│   │   ├── thumbnails.py        # GET /thumbnails/{filename}
│   │   └── stats.py             # GET /stats (counts, weekly digest)
│   └── templates/               # Jinja2 HTML templates (optional: or pure JSON API)
│       └── index.html
│
├── thumbnails/                  # NEW: Saved thumbnail JPEG files (filesystem)
│
├── known_faces/                 # Enrolled face images (existing)
├── debug_frames/                # Debug frame dumps (existing)
│
├── enroll_face.py               # Utility: add known faces (existing)
├── discover_devices.py          # Utility: find SwitchBot device IDs (existing)
│
├── requirements.txt
├── .env
└── .env.example
```

### Structure Rationale

- **`dashboard/` as sub-package:** Keeps all web-serving code isolated from the polling pipeline. FastAPI app can be imported by main.py without polluting top-level namespace.
- **`event_store.py` as shared service:** Both polling loop (write) and dashboard routes (read) import the same module. Avoids duplicate connection handling and makes the boundary explicit.
- **`object_detector.py` as thin wrapper:** Keeps YOLOv8's synchronous `model.predict()` behind an async interface that uses `loop.run_in_executor()`. Callers stay fully async.
- **`telegram_alerter.py` as standalone module:** Telegram `Bot` object initialized once, reused across alerts. Avoids re-instantiating per event.
- **`thumbnails/` as filesystem:** JPEG thumbnails ~30-100KB are stored as files; `event_store.py` stores file path strings. Full-resolution frames never persisted.

## Architectural Patterns

### Pattern 1: Single Event Loop, Multiple asyncio Tasks

**What:** The main entry point starts one asyncio event loop. FastAPI (via uvicorn) and the Ring polling loop run as concurrent `asyncio.Task` objects within that loop. They share memory safely through the shared services layer.

**When to use:** Always, for this project. Avoids the complexity of inter-process communication while keeping both the HTTP server and polling loop responsive.

**Why this works:** The polling loop's blocking operations (YOLOv8 inference, face recognition) are offloaded to a thread executor, so they never block the event loop. FastAPI requests continue to be served while inference runs in a thread.

**Confidence:** HIGH — FastAPI official docs confirm `asyncio.create_task()` inside a lifespan context manager is the correct pattern for long-lived background loops.

```python
# main.py — recommended entry point pattern
from contextlib import asynccontextmanager
import asyncio
import uvicorn
from dashboard.app import create_app
from ring_client import RingClient
from object_detector import ObjectDetector
from event_store import EventStore

@asynccontextmanager
async def lifespan(app):
    # Initialize shared services
    store = EventStore("events.db")
    await store.initialize()

    detector = ObjectDetector()        # loads YOLOv8 weights once
    ring = RingClient(config)
    await ring.authenticate()

    # Start polling loop as background task
    polling_task = asyncio.create_task(
        polling_loop(ring, detector, store, alerter)
    )
    app.state.store = store
    yield
    # Shutdown
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    await store.close()

if __name__ == "__main__":
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=8000)
```

### Pattern 2: Executor Offload for CPU-Bound Inference

**What:** YOLOv8's `model.predict()` is synchronous and CPU-bound. It must never be called with `await` directly or placed in an async function without an executor, or it will block the event loop and stall all HTTP requests.

**When to use:** Any call to `model.predict()` (YOLOv8), `face_recognition.face_encodings()` (dlib), or `cv2` frame extraction.

**Confidence:** HIGH — Python official asyncio docs confirm `loop.run_in_executor()` with `ThreadPoolExecutor` is the correct pattern for CPU-bound blocking calls.

```python
# object_detector.py
import asyncio
from concurrent.futures import ThreadPoolExecutor
from ultralytics import YOLO

class ObjectDetector:
    def __init__(self, model_path="yolov8n.pt"):
        self.model = YOLO(model_path)  # load once at startup
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def detect(self, frame_bytes: bytes) -> list[Detection]:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            self._executor,
            self._predict_sync,
            frame_bytes
        )
        return results

    def _predict_sync(self, frame_bytes: bytes) -> list[Detection]:
        # Pure synchronous — safe to call from executor
        results = self.model.predict(frame_bytes, verbose=False)
        detections = []
        for r in results:
            for box in r.boxes:
                detections.append(Detection(
                    label=self.model.names[int(box.cls)],
                    confidence=float(box.conf),
                    bbox=box.xyxy[0].tolist()
                ))
        return detections
```

**Thread safety note:** Use `max_workers=1` for the YOLO executor. Ultralytics models are not guaranteed thread-safe across concurrent calls. The same executor serializes requests naturally.

### Pattern 3: Telegram as Fire-and-Forget Async Call

**What:** Use `python-telegram-bot`'s `Bot` class directly (without `Application.run_polling()`). Call `await bot.send_message()` or `await bot.send_photo()` from the existing asyncio event loop. No separate event loop or thread needed.

**When to use:** Anytime the polling loop detects a stranger, completes an unlock, or fires a weekly digest.

**Confidence:** HIGH — confirmed by python-telegram-bot maintainers in GitHub discussion #3310: "Use Bot.send_message() directly from your existing event loop."

```python
# telegram_alerter.py
from telegram import Bot

class TelegramAlerter:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id

    async def send_alert(self, message: str, photo_bytes: bytes | None = None):
        if photo_bytes:
            await self.bot.send_photo(
                chat_id=self.chat_id,
                photo=photo_bytes,
                caption=message
            )
        else:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message
            )
```

### Pattern 4: Multi-Camera via asyncio.gather on Per-Camera Coroutines

**What:** Each Ring camera gets its own polling coroutine. `asyncio.gather()` runs all camera coroutines concurrently within the same event loop. Camera identity is threaded through to the EventStore so all events are tagged with `camera_id`.

**When to use:** When adding a second Ring camera. The architecture is designed for this from the start even if only one camera is active initially.

**Confidence:** MEDIUM — Pattern derived from asyncio documentation and multi-camera surveillance patterns; specific Ring multi-camera behavior with ring-doorbell SDK needs validation during implementation.

```python
# main.py — multi-camera polling
async def polling_loop(cameras: list[CameraConfig], ...):
    tasks = [
        asyncio.create_task(poll_single_camera(cam, store, detector, alerter))
        for cam in cameras
    ]
    await asyncio.gather(*tasks)

async def poll_single_camera(cam: CameraConfig, store, detector, alerter):
    ring = RingClient(cam)
    await ring.authenticate()
    while True:
        recording_id = await ring.wait_for_event()
        if recording_id:
            await process_event(recording_id, cam.camera_id, ring, detector, store, alerter)
```

## Data Flow

### Primary Event Processing Flow

```
Ring Motion Detected
    |
    v
ring.wait_for_event() → recording_id
    |
    v
ring.capture_frame(recording_id) → JPEG bytes
    |
    +---> [asyncio.run_in_executor] ObjectDetector.detect() → [person, dog, car, ...]
    |
    +---> [asyncio.run_in_executor] FaceRecognizer.identify() → name | None
    |
    v
EventStore.write_event(
    camera_id, timestamp, recording_id,
    detections=[...], person_name, thumbnail_path
)
    |
    +---> SQLite INSERT INTO events + detections
    +---> Save JPEG thumbnail to thumbnails/{timestamp}.jpg
    |
    v
[if person_name == None AND "person" in detections]
    TelegramAlerter.send_alert("Stranger at door", photo=thumbnail)
    |
[if person_name in known_faces]
    SwitchBotClient.unlock() → cooldown update
    TelegramAlerter.send_alert("Unlocked for {name}")
```

### Dashboard Read Flow

```
Browser → GET /events?limit=50&label=person
    |
    v
FastAPI route handler (async)
    |
    v
EventStore.query_events(filters) → list[EventRow]
    |
    v
JSON response with thumbnail URLs + detection labels
    |
    v
Browser → GET /thumbnails/1708000000_abc.jpg
    |
    v
FastAPI FileResponse from thumbnails/ directory
```

### Heatmap Aggregation Flow

```
Browser → GET /heatmap?period=7d
    |
    v
EventStore.query_heatmap(start, end) →
    SELECT hour, day_of_week, COUNT(*) FROM events GROUP BY hour, day_of_week
    |
    v
JSON response: [{hour: 8, dow: 1, count: 14}, ...]
    |
    v
Browser renders grid visualization (JS or server-side SVG)
```

## SQLite Schema Design

**Confidence:** HIGH for schema shape; MEDIUM for index design (depends on actual query patterns at runtime).

### Recommended Schema

```sql
-- Core event table: one row per Ring motion event
CREATE TABLE events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL DEFAULT 'front_door',
    recorded_at     TEXT NOT NULL,            -- ISO 8601 UTC timestamp
    recording_id    TEXT,                     -- Ring recording ID (nullable)
    person_name     TEXT,                     -- NULL = stranger or no face
    unlock_granted  INTEGER NOT NULL DEFAULT 0, -- boolean
    thumbnail_path  TEXT,                     -- relative path: thumbnails/xxx.jpg
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Detection table: one row per detected object per event
CREATE TABLE detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,       -- 'person', 'dog', 'car', 'package', etc.
    confidence  REAL NOT NULL,       -- 0.0-1.0
    bbox_x1     REAL,
    bbox_y1     REAL,
    bbox_x2     REAL,
    bbox_y2     REAL
);

-- Known persons: maps face name to display info
CREATE TABLE persons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,   -- matches known_faces/ directory prefix
    display_name TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for dashboard query patterns
CREATE INDEX idx_events_recorded_at ON events(recorded_at);
CREATE INDEX idx_events_camera_id   ON events(camera_id);
CREATE INDEX idx_events_person_name ON events(person_name);
CREATE INDEX idx_detections_label   ON detections(label);
CREATE INDEX idx_detections_event_id ON detections(event_id);
```

**Thumbnail storage decision:** Store file path (string) in `events.thumbnail_path`, files on filesystem in `thumbnails/`. Thumbnails ~30-100KB each. SQLite official benchmarks show BLOBs under 100KB read 35% faster from DB than filesystem, but file paths keep the database small and thumbnails independently accessible by FastAPI `FileResponse`. At single-home event volume (< 200 events/day), either approach works; filesystem paths win on operational simplicity.

## Anti-Patterns

### Anti-Pattern 1: Calling model.predict() Directly in an Async Function

**What people do:** `detections = await asyncio.coroutine(model.predict(frame))` or simply calling `model.predict(frame)` inside an `async def` without an executor.

**Why it's wrong:** YOLOv8's `model.predict()` is a synchronous, CPU-bound call that can take 200-2000ms on Raspberry Pi 4. Calling it directly in an async function blocks the event loop for that entire duration. All FastAPI HTTP requests stall. The Ring polling loop stalls. Telegram alerts don't fire.

**Do this instead:** Use `await loop.run_in_executor(executor, model.predict, frame)`. The thread executor runs the blocking call without touching the event loop.

### Anti-Pattern 2: Using Application.run_polling() for Telegram

**What people do:** Create a `python-telegram-bot` `Application`, call `application.run_polling()` to start the bot.

**Why it's wrong:** `run_polling()` blocks the event loop until a stop signal is received. It cannot coexist with an already-running asyncio loop (the polling loop or uvicorn). This causes `RuntimeError: This event loop is already running`.

**Do this instead:** Instantiate `Bot(token=...)` directly. Call `await bot.send_message()` from the existing loop. No `Application`, no `run_polling()`. If you need to receive commands from Telegram (e.g., `/lock`, `/status`), start `Application` manually via `initialize()` + `start()` + `updater.start_polling()` as separate tasks — not via `run_polling()`.

### Anti-Pattern 3: One SQLite Connection Per Request

**What people do:** Open a new `sqlite3.connect()` or `aiosqlite.connect()` inside each FastAPI route handler or each polling loop iteration.

**Why it's wrong:** SQLite connection setup has overhead (~1-5ms). More critically, SQLite has limited write concurrency. Opening multiple simultaneous connections from different async tasks with different transaction contexts causes `database is locked` errors when the polling loop writes while a dashboard route also writes.

**Do this instead:** Open one `aiosqlite` connection at startup (in the lifespan). Share it via `app.state.db` or inject it via dependency. Use `WAL mode` (`PRAGMA journal_mode=WAL`) to allow concurrent readers while the polling loop writes.

```python
# event_store.py — one connection, WAL mode
async def initialize(self):
    self.db = await aiosqlite.connect(self.db_path)
    await self.db.execute("PRAGMA journal_mode=WAL")
    await self.db.execute("PRAGMA foreign_keys=ON")
    await self.db.commit()
```

### Anti-Pattern 4: Running Two Separate Processes (Polling + Dashboard)

**What people do:** Run `python main.py` in one terminal and `python dashboard.py` in another. They communicate via a shared SQLite file.

**Why it's wrong:** Works for read-only dashboard, but creates coordination problems: the polling loop and dashboard may both try to write simultaneously with `SQLITE_BUSY` errors unless WAL mode is set. Environment management becomes duplicated. Startup/shutdown is uncoordinated.

**Do this instead:** Single process entry point. FastAPI serves HTTP. Ring polling loop runs as an asyncio task within the same event loop. Single aiosqlite connection (with WAL mode) shared across both. One `systemd` unit to manage.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Ring Doorbell API | ring-doorbell SDK; async polling via `wait_for_event()` | Existing; polling interval 2-5s |
| SwitchBot Cloud API | HMAC-SHA256 signed HTTP; synchronous `requests` calls wrapped in executor | Existing; network I/O, keep in executor |
| Telegram Bot API | `python-telegram-bot` Bot class; `await bot.send_message()` from existing loop | New; do NOT use run_polling() |
| YOLOv8 / ultralytics | Synchronous `model.predict()`; wrap in ThreadPoolExecutor | New; CPU-bound; must use executor |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| polling loop → event_store | Direct async method calls (`await store.write_event(...)`) | Same process, same event loop |
| dashboard routes → event_store | Direct async method calls via `app.state.store` | Same process; reads are concurrent with writes via WAL |
| object_detector → polling loop | Awaitable result returned from executor | executor serializes YOLO calls |
| telegram_alerter → polling loop | Fire-and-forget `asyncio.create_task(alerter.send_alert(...))` | Non-blocking; don't await in hot path |
| polling loop → switchbot_client | `await loop.run_in_executor(None, switchbot.unlock)` | requests is sync; keep in executor |

## Scalability Considerations

This system targets a single household on Raspberry Pi 4. Scalability questions are about hardware constraints, not user count.

| Concern | At current (1 camera, RPi 4) | At 2-3 cameras (RPi 4) | At 4+ cameras or Pi 5 |
|---------|-------------------------------|------------------------|----------------------|
| YOLOv8 inference time | ~500-1500ms on RPi 4 CPU | Per-camera executor tasks queue; inference serialized | Consider YOLOv8n (nano) or Pi 5 / GPU accelerator |
| SQLite write concurrency | Single writer fine in WAL mode | Still fine; events are infrequent (< 1/min typical) | Still fine; SQLite handles 100+ writes/sec |
| Memory (YOLO model) | YOLOv8n ~6MB RAM, YOLOv8s ~22MB | One model instance shared across cameras | Monitor; Pi 4 has 4GB |
| Dashboard response time | Instant for < 10K events | Add indexes; still fast | Add date-range partitioning |

### Scaling Priorities

1. **First bottleneck:** YOLOv8 inference latency on CPU. Fix: use `yolov8n.pt` (nano, fastest), not `yolov8m.pt` or larger. On Pi 4, nano model is required.
2. **Second bottleneck:** Thumbnail disk space. At 200 events/day × 50KB = 10MB/day = 3.6GB/year. Fix: auto-delete thumbnails older than 30 days via a scheduled cleanup task.

## Build Order Implications

Based on component dependencies, recommended implementation sequence:

1. **EventStore + schema** — All other new components depend on writing to and reading from the database. Build and test this first in isolation.
2. **ObjectDetector (YOLOv8 wrapper)** — Independent of dashboard and Telegram. Test detection output format before integrating into pipeline.
3. **Extend polling loop** — Wire ObjectDetector into the existing Ring → face recognize → SwitchBot loop. Add EventStore writes here. This is the core integration milestone.
4. **TelegramAlerter** — Add after polling loop writes events, so alert logic can reference detection results.
5. **FastAPI dashboard** — Can be built in parallel with steps 3-4 since it only reads from EventStore. Serve static thumbnails + JSON endpoints.
6. **Multi-camera support** — Refactor polling loop to accept camera list after single-camera path is proven.

## Sources

- FastAPI lifespan + background tasks: https://fastapi.tiangolo.com/advanced/events/ (official docs, HIGH confidence)
- python-telegram-bot with existing event loop: https://github.com/python-telegram-bot/python-telegram-bot/discussions/3310 (maintainer-confirmed, HIGH confidence)
- asyncio run_in_executor for blocking calls: https://docs.python.org/3/library/asyncio-eventloop.html (official Python docs, HIGH confidence)
- SQLite BLOB vs file path: https://sqlite.org/intern-v-extern-blob.html + https://sqlite.org/fasterthanfs.html (official SQLite docs, HIGH confidence)
- YOLOv8 Python predict interface: https://docs.ultralytics.com/usage/python/ (official Ultralytics docs, HIGH confidence)
- aiosqlite async SQLite bridge: https://aiosqlite.omnilib.dev/en/latest/ (official docs, HIGH confidence)
- Multi-camera asyncio patterns: community patterns + asyncio official docs (MEDIUM confidence; validate Ring SDK multi-doorbell behavior during implementation)

---
*Architecture research for: Python home surveillance analytics platform*
*Researched: 2026-02-24*
