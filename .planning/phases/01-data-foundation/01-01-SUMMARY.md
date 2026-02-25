---
phase: 01-data-foundation
plan: 01
subsystem: data-layer
tags: [sqlite, aiosqlite, wal-mode, event-store, schema, thumbnails]
requirements: [DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06]

dependency_graph:
  requires: []
  provides:
    - EventStore class with async CRUD
    - SQLite schema (events, detections, persons tables)
    - WAL mode database connection
    - Thumbnail storage directory
  affects:
    - Phase 2: Object Detection (reads/writes events and detections)
    - Phase 3: Pipeline Integration (writes events from Ring polling)
    - Phase 4: Telegram Alerts (reads events for alert context)
    - Phase 5: Web Dashboard (reads events and detections for display)

tech_stack:
  added:
    - aiosqlite==0.22.1
    - aiofiles==24.1.0 (already present at 25.1.0, pinned in requirements.txt)
  patterns:
    - WAL mode SQLite for concurrent read/write
    - aiosqlite.Row as row_factory for dict-like row access
    - Synchronous Pillow thumbnail save before async DB write
    - executemany for batch detection inserts (atomic with event)

key_files:
  created:
    - event_store.py
    - thumbnails/.gitkeep
  modified:
    - requirements.txt
    - config.py
    - .env.example

decisions:
  - WAL mode PRAGMAs applied before DDL (required for WAL to activate on existing DB)
  - save_thumbnail() is synchronous (Pillow is CPU-bound, called before async write)
  - Thumbnail failure isolated from event storage (try/except returns None)
  - unlock_granted stored as INTEGER (0/1) matching SQLite boolean convention

metrics:
  duration: "1m 47s"
  completed: "2026-02-25"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 3
---

# Phase 1 Plan 01: EventStore Data Foundation Summary

**One-liner:** Async SQLite EventStore with WAL mode, full events/detections/persons schema, and JPEG thumbnail storage.

---

## What Was Built

The `EventStore` class is the single database interface for the entire Smart Lock Analytics Platform. It wraps an `aiosqlite` connection configured in WAL mode (enabling concurrent reads during pipeline writes) and provides:

- **Schema:** Three tables (`events`, `detections`, `persons`) with 5 query-optimized indexes
- **Write path:** `write_event()` atomically inserts an event and all its YOLO detections in one transaction
- **Read path:** `get_event()` and `get_recent_events()` return event dicts with nested detection lists
- **Thumbnail path:** `save_thumbnail()` saves Pillow-decoded JPEG files; failures are isolated and never block event storage
- **Lifecycle:** `initialize()` creates schema on first run; `close()` releases the connection cleanly

---

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Install dependencies and update config | c928aaf | requirements.txt, config.py, .env.example, thumbnails/.gitkeep |
| 2 | Implement EventStore module with full schema and CRUD | da2f6ac | event_store.py |

---

## Verification Results

- `import aiosqlite` — OK (version 0.22.1)
- `import aiofiles` — OK (version 25.1.0)
- `Config.DB_PATH` = `"./events.db"` — OK
- `Config.THUMBNAILS_DIR` = `"./thumbnails"` — OK
- `PRAGMA journal_mode` returns `"wal"` — CONFIRMED
- Write event with full fields (person_name, face_confidence, face_distance, unlock_granted, door_action, recording_id) — OK
- Read back with `get_event()` — all fields intact, detections nested — OK
- `get_recent_events(limit=5)` returns 1 event with detections — OK
- `pip check` — no broken requirements

---

## Deviations from Plan

### Auto-fixed Issues

None - plan executed exactly as written.

### Observations

- `aiofiles` was already installed at version 25.1.0 (higher than the pinned 24.1.0 in requirements.txt). Since 25.1.0 is backward-compatible and `pip check` reports no conflicts, no downgrade was performed. The requirements.txt pin of `aiofiles==24.1.0` remains as the minimum validated version for reproducible installs.

---

## Self-Check: PASSED

| Item | Status |
|------|--------|
| event_store.py | FOUND |
| thumbnails/.gitkeep | FOUND |
| requirements.txt | FOUND |
| config.py | FOUND |
| .env.example | FOUND |
| Commit c928aaf (Task 1) | FOUND |
| Commit da2f6ac (Task 2) | FOUND |
