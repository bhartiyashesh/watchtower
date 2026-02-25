# Requirements: Smart Lock Analytics Platform

**Defined:** 2026-02-24
**Core Value:** Automatically unlock the door for recognized household members while building a comprehensive activity log of everything happening at your doorstep — accessible through a searchable web dashboard with real-time Telegram alerts.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Data Storage

- [x] **DATA-01**: System persists every motion event to SQLite with timestamp, camera ID, event type, and recording ID
- [x] **DATA-02**: System stores YOLO detection results (object class, confidence, bounding box) linked to each event
- [x] **DATA-03**: System saves event thumbnail as JPEG file on disk with path stored in event record
- [x] **DATA-04**: System logs every unlock/lock action with timestamp, person name, confidence, and success status
- [x] **DATA-05**: SQLite database uses WAL mode for concurrent read/write access without locking
- [x] **DATA-06**: System stores face recognition results (matched person, distance, confidence) per event

### Object Detection

- [x] **YOLO-01**: System runs YOLO11n object detection on every captured doorbell frame
- [x] **YOLO-02**: System detects and labels: people, dogs, cats, cars, and packages
- [x] **YOLO-03**: YOLO inference runs in a thread executor so it does not block the async event loop
- [x] **YOLO-04**: System stores detection class and confidence score per detected object in the event record
- [x] **YOLO-05**: Face recognition runs on YOLO-cropped person bounding boxes instead of full frame

### Telegram Alerts

- [ ] **TELE-01**: System sends Telegram alert with thumbnail when an unrecognized person is detected at the door
- [ ] **TELE-02**: System sends Telegram confirmation when a known person is recognized and the door is unlocked
- [ ] **TELE-03**: Telegram alerts include event thumbnail photo and descriptive caption in a single message
- [ ] **TELE-04**: System implements 60-second alert coalescing to prevent Telegram flood during burst events
- [ ] **TELE-05**: System respects Telegram rate limits using AIORateLimiter

### Web Dashboard

- [ ] **DASH-01**: User can view a scrollable event history feed with thumbnail, timestamp, detected objects, and recognized person
- [ ] **DASH-02**: User can filter events by date range (today, last 7 days, last 30 days)
- [ ] **DASH-03**: User can filter events by object type (person, dog, cat, car, package)
- [ ] **DASH-04**: Dashboard shows a summary widget with today's event count, last event card, and current lock status
- [ ] **DASH-05**: Dashboard serves event thumbnails securely (no unauthenticated access)
- [ ] **DASH-06**: Dashboard uses server-side pagination for event history (not loading all events at once)
- [ ] **DASH-07**: Dashboard is protected by HTTP Basic Auth

### Pipeline Integration

- [x] **PIPE-01**: FastAPI owns the asyncio event loop with Ring polling running as a background task via lifespan context
- [x] **PIPE-02**: Every motion event flows through: Ring capture → YOLO detection → face recognition → event store write → alert send
- [x] **PIPE-03**: Existing unlock behavior (face match → SwitchBot unlock → cooldown) is preserved unchanged
- [x] **PIPE-04**: System architecture supports adding multiple cameras via camera_id field (multi-camera ready)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Analytics

- **ANLYT-01**: User can view a traffic heatmap showing activity patterns by hour-of-day and day-of-week
- **ANLYT-02**: Dashboard shows object class breakdown widget (count by detected class for selected time period)
- **ANLYT-03**: User can search events by person name

### Automation

- **AUTO-01**: User can define per-person automation rules (IF person == X THEN unlock/lock/alert/ignore)
- **AUTO-02**: System auto-locks when an unrecognized person is detected and door was previously unlocked
- **AUTO-03**: User can configure automation rules via config file or dashboard UI

### Telegram Commands

- **TCMD-01**: User can send /status to Telegram bot to get current system and lock status
- **TCMD-02**: User can send /last_visitor to see the most recent visitor with thumbnail
- **TCMD-03**: User can send /lock and /unlock to remotely control the door

### Digest

- **DGST-01**: System sends weekly activity digest via Telegram summarizing event counts, top visitors, and peak activity times

## Out of Scope

| Feature | Reason |
|---------|--------|
| Live video streaming / RTSP | Ring battery doorbells cannot stream via RTSP; Ring app handles live view |
| Push notifications (FCM/APNS) | FCM fails on macOS and Raspberry Pi (confirmed); Telegram is the chosen channel |
| Cloud sync / remote access portal | Local-first deployment on Raspberry Pi; Telegram provides remote access |
| Multi-property support | Triples codebase scope for zero v1 benefit; single home only |
| Continuous video recording / DVR | Ring battery doorbells only provide event-triggered recordings |
| Emotion/behavior analysis | High false-positive rates, ethical concerns, unreliable on consumer hardware |
| Mobile native app | Web dashboard + Telegram bot cover all use cases |
| Frequent visitor clustering | High complexity (face embedding DBSCAN); deferred to v2+ after data accumulates |
| Detection zone configuration UI | Canvas-based zone drawing; not needed for single-doorbell setup |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 1 | Complete (01-01) |
| DATA-02 | Phase 1 | Complete (01-01) |
| DATA-03 | Phase 1 | Complete (01-01) |
| DATA-04 | Phase 1 | Complete (01-01) |
| DATA-05 | Phase 1 | Complete (01-01) |
| DATA-06 | Phase 1 | Complete (01-01) |
| YOLO-01 | Phase 2 | Complete (02-01, 02-02) |
| YOLO-02 | Phase 2 | Complete (02-01, 02-02) |
| YOLO-03 | Phase 2 | Complete (02-01, 02-02) |
| YOLO-04 | Phase 2 | Complete (02-01, 02-02) |
| YOLO-05 | Phase 2 | Complete (02-01, 02-02) |
| PIPE-01 | Phase 3 | Complete (03-01) |
| PIPE-02 | Phase 3 | Complete (03-01) |
| PIPE-03 | Phase 3 | Complete (03-01) |
| PIPE-04 | Phase 3 | Complete (03-01) |
| TELE-01 | Phase 4 | Pending |
| TELE-02 | Phase 4 | Pending |
| TELE-03 | Phase 4 | Pending |
| TELE-04 | Phase 4 | Pending |
| TELE-05 | Phase 4 | Pending |
| DASH-01 | Phase 5 | Pending |
| DASH-02 | Phase 5 | Pending |
| DASH-03 | Phase 5 | Pending |
| DASH-04 | Phase 5 | Pending |
| DASH-05 | Phase 5 | Pending |
| DASH-06 | Phase 5 | Pending |
| DASH-07 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 27 total
- Mapped to phases: 27
- Unmapped: 0

---
*Requirements defined: 2026-02-24*
*Last updated: 2026-02-25 — DATA-01 through DATA-06 complete after 01-01-PLAN.md*
*Last updated: 2026-02-25 — YOLO-01 through YOLO-05 implementation complete (02-01) and validated by 16-test suite (02-02). Phase 2 complete.*
*Last updated: 2026-02-25 — PIPE-01 through PIPE-04 complete after 03-01-PLAN.md (FastAPI lifespan + full pipeline).*
