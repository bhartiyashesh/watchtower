# Roadmap: Smart Lock Analytics Platform

**Created:** 2026-02-25
**Depth:** Standard
**Coverage:** 27/27 v1 requirements mapped

---

## Phases

- [ ] **Phase 1: Data Foundation** - SQLite schema and EventStore module that every other feature depends on
- [ ] **Phase 2: Object Detection** - YOLO11n object detector wrapped behind async interface, benchmarked on target hardware
- [ ] **Phase 3: Pipeline Integration** - Full motion event pipeline wired end-to-end with persistent event records
- [ ] **Phase 4: Telegram Alerts** - Real-time stranger and unlock alerts with flood control
- [ ] **Phase 5: Web Dashboard** - Authenticated FastAPI dashboard with event history, filters, and summary widget

---

## Phase Details

### Phase 1: Data Foundation
**Goal**: Structured event storage exists and is safe for concurrent access
**Depends on**: Nothing (first phase)
**Requirements**: DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06
**Success Criteria** (what must be TRUE):
  1. A motion event record including timestamp, camera ID, event type, and recording ID can be written to SQLite and read back without error
  2. YOLO detection results (class, confidence, bounding box) can be stored linked to an event record and retrieved by event ID
  3. A JPEG thumbnail file is saved to disk and its path is stored in the event record — no thumbnail data is stored as a BLOB
  4. An unlock/lock action is logged with person name, confidence, and success status and appears in the events table
  5. Concurrent read and write operations against the database complete without "database is locked" errors (WAL mode confirmed active)
**Plans:** 2 plans
Plans:
- [x] 01-01-PLAN.md — EventStore module with full schema, WAL mode, and CRUD methods
- [ ] 01-02-PLAN.md — Comprehensive test suite validating all DATA requirements and concurrent access

### Phase 2: Object Detection
**Goal**: YOLO11n detects objects in doorbell frames without blocking the asyncio event loop
**Depends on**: Phase 1 (event records must exist before detections can be linked)
**Requirements**: YOLO-01, YOLO-02, YOLO-03, YOLO-04, YOLO-05
**Success Criteria** (what must be TRUE):
  1. A doorbell frame containing a person, dog, cat, car, or package is correctly labeled by the detector with class name and confidence score
  2. YOLO inference completes in under 250ms per frame on Raspberry Pi 4 with NCNN model (benchmarked on actual hardware)
  3. Ring polling timestamps do not increase during YOLO inference — confirmed by log inspection that inference runs in executor and does not stall the event loop
  4. Face recognition runs on the YOLO-cropped person bounding box, not the full frame — confirmed by checking that face matching input dimensions match the YOLO crop dimensions
**Plans**: TBD

### Phase 3: Pipeline Integration
**Goal**: Every Ring motion event flows through detection, recognition, and persistence automatically
**Depends on**: Phase 1 (EventStore), Phase 2 (ObjectDetector)
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04
**Success Criteria** (what must be TRUE):
  1. A Ring doorbell motion event triggers YOLO detection, face recognition, EventStore write, and thumbnail save — in that order — confirmed by inspecting the event record and thumbnail file on disk
  2. The existing unlock behavior (face match triggers SwitchBot unlock with cooldown) continues to function after pipeline integration with no regressions
  3. FastAPI starts cleanly with the Ring polling loop running as a background asyncio Task inside the lifespan context — no "loop already running" or "nest_asyncio" errors in startup logs
  4. The event record includes a camera_id field populated for every event, making the schema ready for a second camera without a schema migration
**Plans**: TBD

### Phase 4: Telegram Alerts
**Goal**: Homeowner receives real-time photo alerts for strangers and unlock confirmations on Telegram
**Depends on**: Phase 3 (event records and thumbnails must exist before alerts can fire)
**Requirements**: TELE-01, TELE-02, TELE-03, TELE-04, TELE-05
**Success Criteria** (what must be TRUE):
  1. When an unrecognized person is detected, a Telegram message is sent containing the event thumbnail photo and a caption in a single message — no separate text-only message
  2. When a known person is recognized and the door is unlocked, a Telegram confirmation message is sent with the person's name and thumbnail
  3. Ten motion events fired within 60 seconds result in at most 2 Telegram messages — coalescing is confirmed by inspecting the Telegram chat history
  4. No RetryAfter errors appear in logs after 100 simulated events — the rate limiter handles API throttling transparently
**Plans**: TBD

### Phase 5: Web Dashboard
**Goal**: Homeowner can review all doorbell activity through a secure, filterable web interface
**Depends on**: Phase 3 (event data pipeline must be writing records before dashboard can display them)
**Requirements**: DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-06, DASH-07
**Success Criteria** (what must be TRUE):
  1. User can open the dashboard URL and see a scrollable event history feed showing thumbnail, timestamp, detected object labels, and recognized person name for each event
  2. User can filter the event feed by date range (today / last 7 days / last 30 days) and by object type (person, dog, cat, car, package) — each filter updates the visible events
  3. Dashboard summary widget shows today's total event count, the most recent event card, and the current lock status
  4. Accessing the dashboard or any thumbnail URL without valid HTTP Basic Auth credentials returns a 401 response — no event data or thumbnails are visible unauthenticated
  5. Loading the event feed with 1,000+ events in the database completes without timeout — server-side pagination confirmed by verifying only one page of results is fetched per request
**Plans**: TBD

---

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Data Foundation | 1/2 | In progress | - |
| 2. Object Detection | 0/? | Not started | - |
| 3. Pipeline Integration | 0/? | Not started | - |
| 4. Telegram Alerts | 0/? | Not started | - |
| 5. Web Dashboard | 0/? | Not started | - |

---

## Coverage Map

| Requirement | Phase |
|-------------|-------|
| DATA-01 | Phase 1 |
| DATA-02 | Phase 1 |
| DATA-03 | Phase 1 |
| DATA-04 | Phase 1 |
| DATA-05 | Phase 1 |
| DATA-06 | Phase 1 |
| YOLO-01 | Phase 2 |
| YOLO-02 | Phase 2 |
| YOLO-03 | Phase 2 |
| YOLO-04 | Phase 2 |
| YOLO-05 | Phase 2 |
| PIPE-01 | Phase 3 |
| PIPE-02 | Phase 3 |
| PIPE-03 | Phase 3 |
| PIPE-04 | Phase 3 |
| TELE-01 | Phase 4 |
| TELE-02 | Phase 4 |
| TELE-03 | Phase 4 |
| TELE-04 | Phase 4 |
| TELE-05 | Phase 4 |
| DASH-01 | Phase 5 |
| DASH-02 | Phase 5 |
| DASH-03 | Phase 5 |
| DASH-04 | Phase 5 |
| DASH-05 | Phase 5 |
| DASH-06 | Phase 5 |
| DASH-07 | Phase 5 |

**Total v1:** 27 requirements | **Mapped:** 27 | **Unmapped:** 0

---

## Key Architectural Decisions (from research)

These decisions are baked into phase structure and must not be reversed:

| Decision | Phase | Rationale |
|----------|-------|-----------|
| SQLite WAL mode + USB SSD storage | Phase 1 | SD card will corrupt under WAL writes; WAL prevents locking under concurrent access |
| YOLO11n NCNN format (not PyTorch .pt) | Phase 2 | PyTorch format is 3-5x too slow on RPi 4 (500ms vs 100-150ms per frame) |
| Single ThreadPoolExecutor for YOLO | Phase 2 | YOLO model is not thread-safe; serialize all inference calls |
| FastAPI lifespan owns event loop; Ring polling as asyncio.Task | Phase 3 | Eliminates event loop conflict — do not use asyncio.run() or nest_asyncio |
| Telegram Bot class directly (not Application.run_polling()) | Phase 4 | Application.run_polling() creates its own event loop and conflicts with FastAPI |
| AIORateLimiter + 60-second coalescing | Phase 4 | Prevents Telegram bot ban from burst event floods |
| FileResponse for thumbnails (not StaticFiles) | Phase 5 | StaticFiles bypasses auth; FileResponse allows Basic Auth check per request |

---
*Roadmap created: 2026-02-25*
