# Phase 1: Data Foundation - Research

**Researched:** 2026-02-25
**Domain:** SQLite async event storage — schema design, WAL-mode concurrency, aiosqlite patterns, thumbnail filesystem storage
**Confidence:** HIGH (schema, WAL mode, and aiosqlite patterns verified against official SQLite docs, aiosqlite official docs, and project architecture research)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DATA-01 | System persists every motion event to SQLite with timestamp, camera ID, event type, and recording ID | `events` table schema with `camera_id`, `recorded_at`, `recording_id`, `event_type` fields; `EventStore.write_event()` method |
| DATA-02 | System stores YOLO detection results (object class, confidence, bounding box) linked to each event | `detections` table with `event_id` FK, `label`, `confidence`, `bbox_x1/y1/x2/y2`; `EventStore.write_detections()` method |
| DATA-03 | System saves event thumbnail as JPEG file on disk with path stored in event record | `thumbnail_path TEXT` column in `events`; `thumbnails/` filesystem directory; Pillow for JPEG write |
| DATA-04 | System logs every unlock/lock action with timestamp, person name, confidence, and success status | `person_name`, `face_confidence`, `unlock_granted`, `door_action` columns in `events` table |
| DATA-05 | SQLite database uses WAL mode for concurrent read/write access without locking | `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000` set at connection init; single shared `aiosqlite` connection |
| DATA-06 | System stores face recognition results (matched person, distance, confidence) per event | `person_name TEXT`, `face_confidence REAL`, `face_distance REAL` columns in `events` table |
</phase_requirements>

---

## Summary

Phase 1 creates the data foundation that every downstream phase depends on. The deliverable is a working `event_store.py` module wrapping an aiosqlite connection in WAL mode, a fully specified SQL schema (three tables: `events`, `detections`, `persons`), all required indexes, a `thumbnails/` directory for JPEG files, and a SIGTERM shutdown handler for clean DB close. No new Python packages beyond `aiosqlite` are required for this phase — the rest of the new stack (FastAPI, ultralytics, python-telegram-bot) is installed but not used until later phases.

The schema design is already fully specified in the project's architecture research (ARCHITECTURE.md). The critical decisions are locked: WAL mode is mandatory to allow concurrent dashboard reads during pipeline writes, thumbnails are stored as filesystem paths (not BLOBs), and the database file must live on a USB SSD (not the SD card) to prevent corruption from WAL writes. The `EventStore` class is the single shared service boundary between the write side (polling loop) and the read side (dashboard routes) — all other components import and call it; nothing else directly touches the SQLite connection.

The implementation is straightforward and well-understood. The only subtle risk is connection initialization ordering: WAL mode and foreign key enforcement PRAGMAs must be set before any table creation or data access. Everything else in this phase is standard SQLite DDL with well-documented aiosqlite async patterns.

**Primary recommendation:** Build `event_store.py` as a class that holds a single `aiosqlite` connection, initializes WAL mode at startup, creates tables with `IF NOT EXISTS`, and exposes async `write_event()`, `write_detections()`, `get_event()`, and `close()` methods. Test it in isolation before wiring it to any other component.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiosqlite | 0.22.1 | Async SQLite bridge | Non-blocking SQLite access from asyncio; v0.22.0 switched from polling to futures for significant speed gains; pure Python, no binary extensions, Python 3.9+ compatible |
| sqlite3 | stdlib | Underlying DB engine | Ships with Python; zero deployment overhead; WAL mode handles the concurrent read/write pattern this project requires |
| Pillow | 10.0.0+ (already installed) | JPEG thumbnail write | Already in requirements.txt; `Image.save(path, "JPEG", quality=85)` for thumbnail persistence |
| aiofiles | 24.x | Async filesystem operations | Needed for non-blocking thumbnail directory creation and file write from async context |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pathlib.Path | stdlib | Path construction | Use for all thumbnail path construction — consistent cross-platform path handling |
| datetime | stdlib | ISO 8601 timestamp generation | `datetime.utcnow().isoformat()` for `recorded_at` column values |
| signal | stdlib | SIGTERM handler | Register SIGTERM handler in `EventStore` to close DB connection cleanly on systemd stop |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw aiosqlite SQL | SQLAlchemy 2.0 async ORM | SQLAlchemy adds complexity and a heavy dependency for 3 tables; raw SQL is preferred at single-home event volume |
| aiosqlite | databases (query-builder library) | `databases` wraps aiosqlite with a query builder API; adds a dependency for no benefit at this scale |
| Filesystem JPEG thumbnails | SQLite BLOBs | SQLite official benchmarks show file paths win on operational simplicity for the dashboard's `FileResponse` pattern; BLOBs are faster for reads under 100KB but keep the database large and prevent independent thumbnail serving |

**Installation (new packages only for Phase 1):**
```bash
pip install aiosqlite==0.22.1 aiofiles==24.1.0
```

---

## Architecture Patterns

### Recommended Project Structure (Phase 1 additions)

```
smart-lock-system/
├── event_store.py          # NEW: aiosqlite wrapper — the entire DB interface
├── thumbnails/             # NEW: filesystem directory for JPEG event thumbnails
├── events.db               # GENERATED: SQLite database (on USB SSD in production)
│
├── main.py                 # EXISTING: will be modified in Phase 3
├── config.py               # EXISTING: add DB_PATH, THUMBNAILS_DIR env vars here
├── ring_client.py          # EXISTING: unchanged
├── face_recognizer.py      # EXISTING: unchanged
├── switchbot_client.py     # EXISTING: unchanged
└── requirements.txt        # UPDATE: add aiosqlite==0.22.1, aiofiles==24.1.0
```

Phase 1 touches only `event_store.py` (new), `thumbnails/` (new directory), `config.py` (add two env vars), and `requirements.txt`. No changes to existing runtime logic.

### Pattern 1: EventStore Class with Single Shared Connection

**What:** A class that holds one `aiosqlite` connection for the lifetime of the process. All read and write operations go through this single connection. WAL mode allows concurrent reads while the polling loop writes.

**When to use:** Always. Never open a second connection. Never open a connection per-request or per-task.

**Example:**
```python
# event_store.py
# Source: aiosqlite official docs https://aiosqlite.omnilib.dev/en/stable/api.html
import asyncio
import aiosqlite
from pathlib import Path
from datetime import datetime, timezone

class EventStore:
    def __init__(self, db_path: str, thumbnails_dir: str = "thumbnails"):
        self.db_path = db_path
        self.thumbnails_dir = Path(thumbnails_dir)
        self.db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection, set PRAGMAs, create tables. Call once at startup."""
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        # WAL mode MUST be set before any table creation
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.commit()
        await self._create_tables()

    async def close(self) -> None:
        """Clean shutdown — called from SIGTERM handler and lifespan cleanup."""
        if self.db:
            await self.db.close()
            self.db = None
```

### Pattern 2: Schema Creation with IF NOT EXISTS

**What:** All `CREATE TABLE` and `CREATE INDEX` statements use `IF NOT EXISTS` so the same `initialize()` call is idempotent — safe to call on first boot (creates tables) and subsequent boots (no-ops).

**When to use:** Always. Never use bare `CREATE TABLE` without `IF NOT EXISTS` in a persistent database.

**Example:**
```sql
-- Source: SQLite official docs https://sqlite.org/lang_createtable.html
-- events table: one row per Ring motion event
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL DEFAULT 'front_door',
    recorded_at     TEXT NOT NULL,            -- ISO 8601 UTC: 2026-02-25T10:30:00
    event_type      TEXT NOT NULL DEFAULT 'motion',  -- 'motion', 'ding'
    recording_id    TEXT,                     -- Ring recording ID (nullable)
    person_name     TEXT,                     -- NULL = stranger; 'unknown' = no face detected
    face_confidence REAL,                     -- face_recognition confidence (0.0-1.0)
    face_distance   REAL,                     -- euclidean distance from best match
    unlock_granted  INTEGER NOT NULL DEFAULT 0,  -- 1 = door was unlocked
    door_action     TEXT,                     -- 'unlocked', 'locked', 'none'
    thumbnail_path  TEXT,                     -- relative: 'thumbnails/2026-02-25T10-30-00.jpg'
    alert_sent      INTEGER NOT NULL DEFAULT 0,  -- 1 = Telegram alert dispatched
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- detections table: one row per detected object per event
CREATE TABLE IF NOT EXISTS detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,       -- 'person', 'dog', 'cat', 'car', 'package'
    confidence  REAL NOT NULL,       -- YOLO confidence 0.0-1.0
    bbox_x1     REAL,                -- bounding box coords (pixel coordinates)
    bbox_y1     REAL,
    bbox_x2     REAL,
    bbox_y2     REAL
);

-- persons table: maps recognized face names to display info
CREATE TABLE IF NOT EXISTS persons (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,   -- matches known_faces/ directory prefix
    display_name TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for dashboard and pipeline query patterns
CREATE INDEX IF NOT EXISTS idx_events_recorded_at  ON events(recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_camera_id    ON events(camera_id);
CREATE INDEX IF NOT EXISTS idx_events_person_name  ON events(person_name);
CREATE INDEX IF NOT EXISTS idx_detections_event_id ON detections(event_id);
CREATE INDEX IF NOT EXISTS idx_detections_label    ON detections(label);
```

### Pattern 3: Thumbnail Save + Path Storage

**What:** Save the JPEG frame to `thumbnails/{safe_timestamp}.jpg` using Pillow, then store only the relative path string in the `events.thumbnail_path` column. Never store image bytes in the database.

**When to use:** Every motion event that yields a frame from Ring. If frame is None (Ring timeout), leave `thumbnail_path` as NULL.

**Example:**
```python
# event_store.py — thumbnail save helper
# Source: SQLite official BLOB vs file path docs https://sqlite.org/intern-v-extern-blob.html
from PIL import Image
import io

def save_thumbnail(self, frame_bytes: bytes, event_timestamp: str) -> str | None:
    """Save JPEG thumbnail to disk. Returns relative path string."""
    try:
        # Sanitize timestamp for use as filename
        safe_ts = event_timestamp.replace(":", "-").replace(".", "-")
        filename = f"{safe_ts}.jpg"
        path = self.thumbnails_dir / filename
        img = Image.open(io.BytesIO(frame_bytes))
        img.save(str(path), "JPEG", quality=85)
        return str(path)  # store relative path like 'thumbnails/2026-02-25T10-30-00.jpg'
    except Exception:
        return None  # thumbnail failure never fails event storage
```

### Pattern 4: Write Event + Detections as Atomic Transaction

**What:** Write the parent `events` row and all child `detections` rows inside a single transaction. If the detections write fails, the event row is also rolled back — no orphaned event rows without detection data.

**When to use:** Every time the pipeline processes a Ring event with YOLO results.

**Example:**
```python
# event_store.py — write event with detections
# Source: aiosqlite docs https://aiosqlite.omnilib.dev/en/stable/api.html
async def write_event(
    self,
    camera_id: str,
    recorded_at: str,
    recording_id: str | None,
    event_type: str = "motion",
    person_name: str | None = None,
    face_confidence: float | None = None,
    face_distance: float | None = None,
    unlock_granted: bool = False,
    door_action: str = "none",
    thumbnail_path: str | None = None,
    detections: list[dict] | None = None,
) -> int:
    """Write one event and its detections. Returns the new event ID."""
    async with self.db.execute(
        """INSERT INTO events (
            camera_id, recorded_at, event_type, recording_id,
            person_name, face_confidence, face_distance,
            unlock_granted, door_action, thumbnail_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            camera_id, recorded_at, event_type, recording_id,
            person_name, face_confidence, face_distance,
            1 if unlock_granted else 0, door_action, thumbnail_path,
        ),
    ) as cursor:
        event_id = cursor.lastrowid

    if detections:
        await self.db.executemany(
            """INSERT INTO detections (event_id, label, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (event_id, d["label"], d["confidence"],
                 d.get("bbox_x1"), d.get("bbox_y1"),
                 d.get("bbox_x2"), d.get("bbox_y2"))
                for d in (detections or [])
            ],
        )

    await self.db.commit()
    return event_id
```

### Pattern 5: Read Event by ID with Detections

**What:** Fetch an event row and its associated detections in two queries. Use `aiosqlite.Row` (via `db.row_factory = aiosqlite.Row`) for dict-like column access.

**Example:**
```python
# event_store.py — read event with detections
async def get_event(self, event_id: int) -> dict | None:
    self.db.row_factory = aiosqlite.Row
    async with self.db.execute(
        "SELECT * FROM events WHERE id = ?", (event_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    event = dict(row)
    async with self.db.execute(
        "SELECT * FROM detections WHERE event_id = ?", (event_id,)
    ) as cursor:
        event["detections"] = [dict(r) for r in await cursor.fetchall()]
    return event
```

### Pattern 6: Config Extension for DB Path and Thumbnails Dir

**What:** Add two new environment variables to `config.py` so the database path and thumbnails directory are configurable (critical for pointing the DB at the USB SSD in production).

**Example:**
```python
# config.py additions
DB_PATH: str = os.getenv("DB_PATH", "./events.db")
THUMBNAILS_DIR: str = os.getenv("THUMBNAILS_DIR", "./thumbnails")
```

And in `.env.example`:
```
# Phase 1: Data Foundation
DB_PATH=/mnt/usb/events.db          # USB SSD path on Raspberry Pi
THUMBNAILS_DIR=/mnt/usb/thumbnails  # USB SSD path on Raspberry Pi
```

### Anti-Patterns to Avoid

- **Opening a new aiosqlite connection per event or per request:** Causes `database is locked` errors when concurrent tasks try to write simultaneously. Use one shared connection for the process lifetime.
- **Setting WAL mode after table creation:** WAL PRAGMA must be the first thing executed on a new connection before any DDL. If set after, existing journal files may be left in rollback mode.
- **Storing thumbnail bytes as a BLOB:** Keeps the database at manageable size, allows independent thumbnail serving by FastAPI `FileResponse`, and avoids the dashboard route having to decode binary from every query result.
- **Using bare `CREATE TABLE` without `IF NOT EXISTS`:** Causes errors on every startup after the first — the database already exists on subsequent runs.
- **Relying on `DEFAULT (datetime('now'))` for event timestamps:** `datetime('now')` uses the database server clock. Store `recorded_at` from the Ring event timestamp (or `datetime.utcnow().isoformat()` at processing time) so timestamps are accurate to the actual event, not the processing time.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async SQLite bridge | Custom threading + queue to wrap sqlite3 | `aiosqlite` | aiosqlite handles all threading, event loop integration, and cursor lifecycle correctly; hand-rolled versions miss edge cases in connection close and error propagation |
| Concurrent write safety | Custom lock/mutex | SQLite WAL mode + single connection | WAL mode is SQLite's purpose-built solution for concurrent readers; `busy_timeout` handles the rare write-write race without application-level locking code |
| Thumbnail JPEG encoding | Raw bytes manipulation | `Pillow` (already installed) | Pillow handles EXIF stripping, color space conversion, and progressive JPEG correctly; already a project dependency |
| Filesystem thumbnail directory | `os.makedirs` with error handling | `Path.mkdir(parents=True, exist_ok=True)` | pathlib handles all edge cases cleanly in one call |
| Schema migration | Custom migration runner | `IF NOT EXISTS` + additive columns | At Phase 1 scale (schema not yet in production), additive changes with `IF NOT EXISTS` are sufficient; no migration framework needed until Phase 5+ when the schema is in production use |

**Key insight:** SQLite's WAL mode is specifically designed for the read-heavy, occasionally-write pattern this project uses (polling loop writes; dashboard reads). The only thing that requires care is connection discipline — one connection shared across the process, WAL set at connection open, and a clean close on shutdown.

---

## Common Pitfalls

### Pitfall 1: "database is locked" Errors Under Concurrent Async Access

**What goes wrong:** Multiple concurrent `await store.write_event()` calls — or a dashboard read happening simultaneously with a pipeline write — cause `sqlite3.OperationalError: database is locked`.

**Why it happens:** Without WAL mode, SQLite uses a rollback journal that allows only one active reader OR writer at a time. Two async tasks touching the database at the same millisecond cause one to fail immediately.

**How to avoid:**
1. Set `PRAGMA journal_mode=WAL` as the first statement after `aiosqlite.connect()`.
2. Set `PRAGMA busy_timeout=5000` — gives blocked transactions up to 5 seconds to wait before failing, instead of failing immediately.
3. Use exactly ONE `aiosqlite` connection for the entire process; never open a second connection.

**Warning signs:** `sqlite3.OperationalError: database is locked` in logs, especially when dashboard and polling loop run concurrently.

### Pitfall 2: SD Card Corruption from WAL Write Amplification

**What goes wrong:** SQLite WAL mode writes a WAL file alongside the database file. On Raspberry Pi SD cards, the write amplification from WAL checkpoint operations can cause file corruption, especially during power loss or forced shutdown.

**Why it happens:** SD cards have limited write endurance and do not implement proper power-fail-safe write semantics. WAL checkpoints (copying WAL back to database file) involve multiple file writes that are not atomic at the hardware level.

**How to avoid:**
- Store `events.db` and `thumbnails/` on a USB SSD (set `DB_PATH=/mnt/usb/events.db` in `.env`).
- Register a `signal.SIGTERM` handler that calls `await store.close()` before process exit — ensures WAL checkpoint completes cleanly.
- Set `PRAGMA synchronous=NORMAL` (not FULL) — faster writes while still safe for non-SD-card storage.

**Warning signs:** Database file size growing unboundedly (WAL not checkpointing), `database disk image is malformed` errors after restart.

### Pitfall 3: Missing face_distance vs face_confidence Distinction

**What goes wrong:** DATA-06 requires storing face recognition results including "distance" and "confidence." The existing `face_recognizer.py` uses `face_recognition.face_distance()` which returns euclidean distance (lower = better match). This is not the same as confidence (higher = better).

**Why it happens:** Conflating distance and confidence when designing the schema, then having to migrate.

**How to avoid:** Store both as separate columns: `face_distance REAL` (raw euclidean distance from `face_recognition.face_distance()`) and `face_confidence REAL` (derived: `1.0 - face_distance`, clamped to 0.0-1.0). This preserves the raw value and provides a human-readable confidence score for the dashboard.

**Warning signs:** Dashboard shows confidence > 1.0 or negative confidence for edge cases.

### Pitfall 4: Thumbnail Path Portability

**What goes wrong:** Thumbnail path stored as absolute path (`/home/pi/smart-lock-system/thumbnails/xxx.jpg`) breaks when the project directory moves or when serving via FastAPI `FileResponse` from a relative base.

**Why it happens:** Using `str(Path(thumbnails_dir).resolve() / filename)` instead of relative path.

**How to avoid:** Store the relative path from the project root: `thumbnails/2026-02-25T10-30-00.jpg`. Reconstruct the absolute path at read time by joining with the configured `THUMBNAILS_DIR` base.

**Warning signs:** 404 errors from FastAPI thumbnail routes after any directory restructuring.

### Pitfall 5: event_type Column Missing

**What goes wrong:** DATA-01 requires storing "event type" (motion vs ding). The current system only processes motion events but the Ring doorbell also fires "ding" (doorbell button press) events. The schema must support both from day one.

**Why it happens:** Assuming "only motion events matter" and omitting the column, then needing a schema change in Phase 3.

**How to avoid:** Include `event_type TEXT NOT NULL DEFAULT 'motion'` in the `events` table from day one. Ring events will set this to `'motion'` or `'ding'`.

---

## Code Examples

Verified patterns from official sources:

### Complete EventStore Initialization (WAL + Tables)

```python
# event_store.py — complete initialize() method
# Source: aiosqlite official docs https://aiosqlite.omnilib.dev/en/stable/api.html
# Source: SQLite WAL docs https://sqlite.org/wal.html

async def initialize(self) -> None:
    self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
    self.db = await aiosqlite.connect(self.db_path)
    self.db.row_factory = aiosqlite.Row

    # PRAGMAs must be first — before any DDL
    await self.db.execute("PRAGMA journal_mode=WAL")
    await self.db.execute("PRAGMA foreign_keys=ON")
    await self.db.execute("PRAGMA busy_timeout=5000")
    await self.db.execute("PRAGMA synchronous=NORMAL")
    await self.db.commit()

    await self.db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id       TEXT NOT NULL DEFAULT 'front_door',
            recorded_at     TEXT NOT NULL,
            event_type      TEXT NOT NULL DEFAULT 'motion',
            recording_id    TEXT,
            person_name     TEXT,
            face_confidence REAL,
            face_distance   REAL,
            unlock_granted  INTEGER NOT NULL DEFAULT 0,
            door_action     TEXT DEFAULT 'none',
            thumbnail_path  TEXT,
            alert_sent      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await self.db.execute("""
        CREATE TABLE IF NOT EXISTS detections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            label       TEXT NOT NULL,
            confidence  REAL NOT NULL,
            bbox_x1     REAL,
            bbox_y1     REAL,
            bbox_x2     REAL,
            bbox_y2     REAL
        )
    """)
    await self.db.execute("""
        CREATE TABLE IF NOT EXISTS persons (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            display_name TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Indexes
    await self.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_recorded_at ON events(recorded_at DESC)"
    )
    await self.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_camera_id ON events(camera_id)"
    )
    await self.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_person_name ON events(person_name)"
    )
    await self.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_detections_event_id ON detections(event_id)"
    )
    await self.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_detections_label ON detections(label)"
    )
    await self.db.commit()
```

### Verifying WAL Mode is Active

```python
# Use sqlite3 CLI to verify — run after EventStore.initialize()
# sqlite3 events.db "PRAGMA journal_mode;"
# Expected output: wal
```

```python
# Or verify programmatically in a test
async with self.db.execute("PRAGMA journal_mode") as cursor:
    row = await cursor.fetchone()
    assert row[0] == "wal", f"WAL mode not active: {row[0]}"
```

### SIGTERM Handler for Clean Shutdown

```python
# main.py — register before starting event loop
# Source: Python signal docs https://docs.python.org/3/library/signal.html
import signal

def register_shutdown(store: EventStore) -> None:
    loop = asyncio.get_event_loop()

    def _shutdown_handler():
        loop.create_task(store.close())

    loop.add_signal_handler(signal.SIGTERM, _shutdown_handler)
```

### Concurrent Read + Write Smoke Test

```python
# Test: proves WAL mode allows concurrent read and write without locking
import asyncio

async def test_concurrent_access(store: EventStore) -> None:
    async def writer():
        for i in range(10):
            await store.write_event(
                camera_id="front_door",
                recorded_at=datetime.utcnow().isoformat(),
                recording_id=f"rec_{i}",
            )
            await asyncio.sleep(0.05)

    async def reader():
        for _ in range(10):
            await store.get_recent_events(limit=5)
            await asyncio.sleep(0.05)

    # Run both concurrently — should complete without "database is locked"
    await asyncio.gather(writer(), reader())
    print("Concurrent access test PASSED")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Flat JSON event log files | SQLite with WAL mode | Standard since SQLite 3.7 (2010), aiosqlite since 2018 | Enables dashboard queries, filtering, and aggregation without parsing flat files |
| SQLite BLOBs for images | Filesystem JPEGs with path in DB | Documented by SQLite official BLOB vs path benchmarks | Keeps DB small, allows independent file serving via FastAPI FileResponse |
| Synchronous sqlite3 in async code | aiosqlite async bridge | aiosqlite 0.22.0 (2025-12) switched polling to futures | No event loop blocking; v0.22.0 is significantly faster than 0.21.x |
| One connection per request | Single shared WAL-mode connection | aiosqlite + WAL design pattern | Eliminates locking, reduces connection overhead |

**Deprecated/outdated:**
- `sqlite3.connect()` directly in asyncio code: Blocks the event loop on every query. Replace with `aiosqlite.connect()`.
- `PRAGMA journal_mode=DELETE` (default): Single-writer, no concurrent readers. WAL mode is required for this architecture.

---

## Open Questions

1. **USB SSD mount path on this specific Raspberry Pi**
   - What we know: DB must be on USB SSD; typical mount is `/mnt/usb/` or `/media/pi/`
   - What's unclear: Whether the USB SSD is mounted and at what path on this specific RPi 4
   - Recommendation: Confirm mount path before Phase 1 implementation; add a config validation check in `Config.validate()` that verifies the `DB_PATH` parent directory exists

2. **httpx version conflict between FastAPI and python-telegram-bot**
   - What we know: Both libraries bring `httpx` as a dependency; FastAPI standard extras and python-telegram-bot 22.6 both require httpx
   - What's unclear: Whether their version ranges conflict after full stack install
   - Recommendation: Run `pip install aiosqlite==0.22.1 aiofiles==24.1.0 "fastapi[standard]==0.133.0" python-telegram-bot==22.6 && pip check` at the start of Phase 1 to surface any conflict early

3. **face_distance range for confidence derivation**
   - What we know: `face_recognition.face_distance()` returns euclidean distance; tolerance default is 0.5 (match if distance <= 0.5)
   - What's unclear: Whether `1.0 - face_distance` is the right confidence formula or if the dashboard expects a different scale
   - Recommendation: Store raw `face_distance` and derive `face_confidence = max(0.0, 1.0 - face_distance)` in the write path; this can be recalculated from raw distance if the formula changes

---

## Sources

### Primary (HIGH confidence)

- [aiosqlite official docs](https://aiosqlite.omnilib.dev/en/stable/api.html) — connection API, row_factory, execute patterns
- [SQLite WAL mode official docs](https://sqlite.org/wal.html) — concurrent read/write behavior, checkpoint mechanics
- [SQLite BLOB vs file path benchmark](https://sqlite.org/intern-v-extern-blob.html) — thumbnail storage decision rationale
- [SQLite PRAGMA reference](https://sqlite.org/pragma.html) — journal_mode, synchronous, busy_timeout, foreign_keys
- [Python asyncio signal handling](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.add_signal_handler) — SIGTERM handler pattern
- `.planning/research/ARCHITECTURE.md` — project-specific schema design, WAL mode decision, EventStore pattern (HIGH confidence — matches official SQLite and aiosqlite documentation)
- `.planning/research/STACK.md` — package versions, Python 3.12 compatibility table, installation commands
- `.planning/research/SUMMARY.md` — phase rationale, known risks, pitfalls specific to this project

### Secondary (MEDIUM confidence)

- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) — version 0.22.1, Python 3.9+ support, v0.22.0 futures changelog
- [Simon Willison: Enabling WAL mode](https://til.simonwillison.net/sqlite/enabling-wal-mode) — WAL enablement pattern verified against official docs
- [SQLite forum: WAL mode with multiple processes](https://sqlite.org/forum/forumpost/c4dbf6ca17) — concurrent access behavior

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — aiosqlite version, WAL PRAGMA patterns, and Pillow thumbnail save all verified against official documentation
- Schema design: HIGH — schema fully specified in project architecture research and cross-verified against SQLite official docs; all column types match SQLite type affinity rules
- Architecture patterns: HIGH — single-connection + WAL pattern confirmed by both official SQLite docs and aiosqlite docs
- Pitfalls: HIGH for locking and SD card corruption (official docs + project research); MEDIUM for face_distance/confidence distinction (derived from face_recognition library docs and existing codebase)

**Research date:** 2026-02-25
**Valid until:** 2026-08-25 (stable — SQLite and aiosqlite APIs are extremely stable; WAL behavior does not change between minor versions)
